#! /usr/bin/python

"""
Pilot application for TEITS demo

What it does :
- Connects to the drone
- Gets the video
- Stores each frame in a folder
- Stores frame indexes in a stream (video_stream) in a topic named with drone id
- reads movements instructions from a stream

What has to be defined :
- the drone ID
- FPS for the transmitted video
- the project folder on the cluster

"""


import time
from time import sleep
import os
import sys
import av
import tellopy
import json
import threading
from random import randint
from confluent_kafka import Producer, Consumer, KafkaError
from mapr.ojai.storage.ConnectionFactory import ConnectionFactory
from math import atan2, sqrt, pi


DRONE_ID = sys.argv[1]
FPS = 20.0
PROJECT_FOLDER = "/teits"


def get_cluster_name():
  with open('/opt/mapr/conf/mapr-clusters.conf', 'r') as f:
    first_line = f.readline()
    return first_line.split(' ')[0]

def create_stream(stream_path):
  if not os.path.islink(stream_path):
    print("stream {} is missing. Exiting.".format(stream_path))
    sys.exit()
    
cluster_name = get_cluster_name()

ROOT_PATH = '/mapr/' + cluster_name + PROJECT_FOLDER

IMAGE_FOLDER = ROOT_PATH + "/" + DRONE_ID + "/images/source/"
VIDEO_STREAM = ROOT_PATH + "/video_stream"
POSITIONS_STREAM = ROOT_PATH + "/positions_stream"
ZONES_TABLE = ROOT_PATH + "/zones_table"
POSITIONS_TABLE = ROOT_PATH + "/positions_table"

# Create database connection
connection_str = "10.0.0.11:5678?auth=basic;user=mapr;password=mapr;ssl=false"
connection = ConnectionFactory().get_connection(connection_str=connection_str)
zones_table = connection.get_or_create_store(ZONES_TABLE)
positions_table = connection.get_or_create_store(POSITIONS_TABLE)

# test if folders exist and create them if needed
if not os.path.exists(IMAGE_FOLDER):
    os.makedirs(IMAGE_FOLDER)

# create sreams if needed
create_stream(VIDEO_STREAM)
create_stream(POSITIONS_STREAM)


# Function for transfering the video frames to FS and Stream
def get_drone_video(drone):
    global FPS
    global DRONE_ID
    global VIDEO_STREAM
    global IMAGE_FOLDER
    print("producing into {}".format(VIDEO_STREAM))
    video_producer = Producer({'streams.producer.default.stream': VIDEO_STREAM})
    current_sec = 0
    last_frame_time = 0
    container = av.open(drone.get_video_stream())
    try:
        start_time = time.time()
        received_frames = 0
        sent_frames = 0
        while True:
            for frame in container.decode(video=0):
                received_frames += 1
                current_time = time.time()
                if current_time > (last_frame_time + float(1/FPS)):
                    frame.to_image().save(IMAGE_FOLDER + "frame-{}.jpg".format(frame.index))
                    video_producer.produce(DRONE_ID, json.dumps({"index":frame.index}))
                    sent_frames += 1
                    last_frame_time = time.time()

                # Print stats every second
                elapsed_time = time.time() - start_time
                if int(elapsed_time) != current_sec:
                    # print("Elapsed : {} s, received {} fps , sent {} fps".format(elapsed_time,received_frames,sent_frames))
                    received_frames = 0
                    sent_frames = 0
                    current_sec = int(elapsed_time)

    # Catch exceptions
    except Exception as ex:
        print(ex)



def move_to_zone(drone,start_zone,drop_zone):
    print("###############      moving from {} to {}".format(start_zone,drop_zone))
    # get start_zone coordinates
    print("get zone")
    current_position_document = zones_table.find_by_id(start_zone)
    print("ok")
    current_position = (float(current_position_document["x"]),float(current_position_document["y"]))
    print(current_position)
    # get drop_zone coordinates
    print("get zone")
    new_position_document = zones_table.find_by_id(drop_zone)
    print("ok")
    new_position = (float(new_position_document["x"]),float(new_position_document["y"]))
    print(new_position)

    # calcul du deplacement
    x = new_position[0] - current_position[0]
    y = new_position[1] - current_position[1]
    # calcul angle de rotation vs axe x
    print(x)
    print(y)
    angle = atan2(y,x)*180/pi
    # rotation
    print("###############      turning {} degrees".format(angle))
    # drone.turn(angle)
    # time.sleep(5)
    # distance a parcourir
    distance = sqrt(x*x + y*y)
    # deplacement
    print("###############      forward {} m".format(distance))
    # drone.forward(distance) # in m
    # time.sleep(5)
    # reset angle
    print("###############      turning {} degrees".format(-angle))
    # drone.turn(-angle)
    # time.sleep(5)
    positions_table.insert_or_replace(doc={'_id': DRONE_ID, "zone":drop_zone, "status":"flying"})


def set_landed():
    current_zone = positions_table.find_by_id(DRONE_ID)["zone"]
    positions_table.insert_or_replace(doc={'_id': DRONE_ID, "zone":current_zone, "status":"landed"})


def main():

    drone = tellopy.Tello()
    set_landed()
    
    # drone.connect()
    # # drone.wait_for_connection(60)

    # # create video thread
    # videoThread = threading.Thread(target=get_drone_video,args=[drone])
    # videoThread.start()


    start_time = time.time()
    flight_time = 300 # seconds
    consumer_group = randint(1000, 100000)
    positions_consumer = Consumer({'group.id': consumer_group,'default.topic.config': {'auto.offset.reset': 'latest'}})
    positions_consumer.subscribe([POSITIONS_STREAM + ":" + DRONE_ID])


    while True:
        print("polling")
        msg = positions_consumer.poll()
        if msg is None:
            print("none")
            continue
        if not msg.error():
            json_msg = json.loads(msg.value().decode('utf-8'))
            print(json_msg)
            from_zone = positions_table.find_by_id(DRONE_ID)["zone"]
            drop_zone = json_msg["drop_zone"]
            
            if json_msg["action"] == "takeoff":
                print("###############      Takeoff")
                # drone.takeoff()
                time.sleep(1)
                positions_table.insert_or_replace(doc={'_id': DRONE_ID, "zone":from_zone, "status":"flying"})

            if drop_zone != from_zone:
                move_to_zone(drone,from_zone,drop_zone)
                
            if json_msg["action"] == "land":
                print("###############      Land")
                # drone.land()
                positions_table.insert_or_replace(doc={'_id': DRONE_ID, "zone":from_zone, "status":"landed"})


        elif msg.error().code() != KafkaError._PARTITION_EOF:
            print(msg.error())

        if time.time() > start_time + flight_time:
            print("Time expired")
            break


    drone.quit()


    sys.exit()


if __name__ == '__main__':
    main()
