import aiohttp
import argparse
import asyncio
import async_frames_cv_v2 as af
import base64
import configparser
import cv2
import datetime
# import httpx
import json
import logging
import numpy as np
import os
from pathlib import Path
import pytz
import redis
from secvision_static import initworkers, channel_names
import sys
import time
from turbojpeg import TurboJPEG

# determine user home directory
home = str(Path.home())

# the tensorrt_demos directory, please build this first
tensorRT = os.path.join(home, "tensorrt_demos/utils")
sys.path.append(tensorRT)

import pycuda.autoinit  # This is needed for initializing CUDA driver

from yolo_classes import get_cls_dict
from visualization import BBoxVisualization
from yolo_with_plugins import TrtYOLO

LOG_LEVEL = logging.INFO
LOGFORMAT = "  %(log_color)s%(levelname)-8s%(reset)s | %(log_color)s%(message)s%(reset)s"
from colorlog import ColoredFormatter

# logging.basicConfig(filename='detections.log')
logging.root.setLevel(LOG_LEVEL)
formatter = ColoredFormatter(LOGFORMAT)
stream = logging.StreamHandler()
stream.setLevel(LOG_LEVEL)
stream.setFormatter(formatter)
log = logging.getLogger('pythonConfig')
log.setLevel(LOG_LEVEL)
log.addHandler(stream)


def parse_args():
    """Parse input arguments."""
    desc = ('Detect persons on HTTP pictures from HikVision DVR')
    parser = argparse.ArgumentParser(description=desc)
    parser.add_argument(
        '-c', '--category_num', type=int, default=80,
        help='number of object categories [80]')
    parser.add_argument(
        '-m', '--model', type=str, required=False, default='yolov4-416',
        help=('[yolov3-tiny|yolov3|yolov3-spp|yolov4-tiny|yolov4|'
              'yolov4-csp|yolov4x-mish]-[{dimension}], where '
              '{dimension} could be either a single number (e.g. '
              '288, 416, 608) or 2 numbers, WxH (e.g. 416x256)'))
    parser.add_argument(
        '-l', '--letter_box', action='store_true',
        help='inference with letterboxed image [False]')
    args = parser.parse_args()
    return args


args = parse_args()
if args.category_num <= 0:
    raise SystemExit('ERROR: bad category_num (%d)!' % args.category_num)
if not os.path.isfile('yolo/%s.trt' % args.model):
    raise SystemExit('ERROR: file (yolo/%s.trt) not found!' % args.model)

cwdpath = os.path.join(home, "Pictures/SecVision/")

xml_on = "<IOPortData version=\"1.0\" xmlns=\"http://www.hikvision.com/ver20/XMLSchema\"><outputState>high</outputState></IOPortData>"
xml_off = "<IOPortData version=\"1.0\" xmlns=\"http://www.hikvision.com/ver20/XMLSchema\"><outputState>low</outputState></IOPortData>"

thresholds = {
    '101': 0.78,
    '201': 0.78,
    '301': 0.82,
    '401': 0.86,
    '501': 0.92,
    '601': 0.92,
    '701': 0.78,
    '801': 0.78
}

draw = {
    '101': True,
    '201': True,
    '301': True,
    '401': True,
    '501': True,
    '601': True,
    '701': True,
    '801': True
}


class SecVisionJetson:
    channel_frames = []
    cnt = 0
    class_channel_event = {}
    class_garbage_collector = []
    zone1 = {}
    zone2 = {}
    zone3 = {}
    zone4 = {}
    network_speed = []
    jpeg = TurboJPEG()
    cls_dict = get_cls_dict(args.category_num)
    vis = BBoxVisualization(cls_dict)
    trt_yolo = TrtYOLO(args.model, args.category_num, args.letter_box)
    

    def __init__(self, cfg, redis) -> None:
        self.config = cfg
        self.redisDb = redis
        self.chcnt = self.config.get('DVR', 'channels')
        self.DVRip = self.config.get('DVR', 'ip')
        # self.dbObj = self.database.all()

    def session_auth(self):
        authkey = f"{self.config.get('DVR', 'username')}:{self.config.get('DVR', 'password')}"
        auth_bytes = authkey.encode('ascii')
        base64_bytes = base64.b64encode(auth_bytes)
        auth = base64_bytes.decode('ascii')
        headers = {"Authorization": f"Basic {auth}"}
        return headers
    
    # DVR has 4 Alarm Output ports, they are electrically connected like so :
    # => Output 1 to Inputs 1 and 2
    # => Output 2 to Inputs 3 and 4
    # => Output 3 to Inputs 5 and 6
    # => Output 4 to Inputs 7 and 8
    def determine_zone(self, channel):
        zone = 1
        if int(channel) <= 201:
            zone = 1
        elif 201 < int(channel) <= 401:
            zone = 2
        elif 401 < int(channel) <= 601:
            zone = 3
        else:
            zone = 4
        return zone

    # Starts a recording for a zone when triggered.
    # TODO : Trigger per channel directly, would negate the surveillance center notification...
    async def trigger_zone(self, session, zone, high):
        url = f"http://{self.DVRip}/ISAPI/System/IO/outputs/{zone}/trigger"
        data = ""
        if high:
            data = xml_on
        else:
            data = xml_off
        async with session.put(url, data=data) as response:
            if response.status == 200:
                if high:
                    logging.warning(
                        f" Zone {zone} triggered on")
                else:
                    logging.warning(
                        f" Zone {zone} triggered off")

    # Network detection
    async def detect(self, image, trt_yolo, conf_th, vis, channel, session, tasks):
        img = image
        start = time.time()
        boxes, confs, clss = trt_yolo.detect(img, conf_th)
        end = time.time()
        if end - start > 0.19:
            logging.info(f" {channel_names[channel]} Detection time slow - {(end - start):.2f}s")
        idx = 0
        persons = 0
        # count persons
        for cococlass in clss:
            if cococlass == 0:
                persons += 1
        if persons > 0 :
            for cococlass in clss:
                if cococlass == 0 and confs[idx] >= thresholds[channel]:
                    now = datetime.datetime.now()
                    timenow = str(now.replace(tzinfo=pytz.utc))
                    zone = self.determine_zone(channel)
                    msg = await self.zone_activator(channel, session, tasks, zone, confs[idx], persons)
                    logging.warning(msg)

                    # Over write latest human timestamp on a given channel
                    self.class_channel_event[channel] = datetime.datetime.timestamp(datetime.datetime.now())
                    # Image saving
                    imgdir = "frames/" + now.strftime('%Y-%m-%d') + "/" + f"{channel}" + "/"
                    wd = os.path.join("../", imgdir)
                    try:
                        os.makedirs(wd)
                    except FileExistsError:
                        # directory already exists
                        pass
                    # Save data
                    drawing = draw[channel]
                    if drawing:
                        img = vis.draw_bboxes(img, boxes, confs, clss)
                    else:
                        pass
                    start = time.time()
                    savepath = wd + f"{now.strftime('%H_%M_%S.%f')}_person_"
                    np.save(wd + f"{now.strftime('%H_%M_%S.%f')}_person_boxes", boxes)
                    np.save(wd + f"{now.strftime('%H_%M_%S.%f')}_person_confs", confs)
                    np.save(wd + f"{now.strftime('%H_%M_%S.%f')}_person_clss", clss)
                    cv2.imwrite(savepath + "frame.jpg", img,
                                [int(cv2.IMWRITE_JPEG_QUALITY), 83])
                    end = time.time()
                    logging.info(f" File save time{(end - start):.2f}s")
                    
                    data = {
                        "time": f"{timenow}",
                        "persons": str(persons),
                        "channel": f"{channel}",
                        "path": f"{savepath}",
                        "confs": str(confs[idx])
                    }
                    # start1 = time.time()
                    # self.database.insert(data)
                    # end1 = time.time()
                    # logging.info(f" Insert time {(end1 - start1):.2f}s")

                    # start2 = time.time()
                    # self.dbObj = self.database.all()
                    # end2 = time.time()
                    # logging.info(f" Sort time {(end2 - start2):.2f}s")
                    
                    start3 = time.time()
                    self.redisDb.rpush(data['channel'], json.dumps(data))
                    end3 = time.time()
                    logging.info(f" Redis time {(end3 - start3):.2f}s")
                    
                    # image_file = Image.open(io.BytesIO(bytes))
                    # image_file.save(wd + f"{now.strftime('%H_%M_%S.%f')}_person_frame.jpg")
                    break
                idx += 1

    async def zone_activator(self, channel, session, tasks, zone, confidence, persons):
        person = f'{persons} persons' if persons > 1 else f'{persons} person'
        msg = f" {channel_names[channel]} - {confidence:.2f} - {person} found in zone {zone} - recording"
        if zone == 1:
            if len(self.zone1) == 0:
                msg = f" {channel_names[channel]} - {confidence:.2f} - {person} found in zone {zone} - start recording"
                tasks.append(asyncio.ensure_future(self.trigger_zone(session, zone, True)))
            self.zone1[channel] = channel
        elif zone == 2:
            if len(self.zone2) == 0:
                msg = f" {channel_names[channel]} - {confidence:.2f} - {person} found in zone {zone} - start recording"
                tasks.append(asyncio.ensure_future(self.trigger_zone(session, zone, True)))
            self.zone2[channel] = channel
        elif zone == 3:
            if len(self.zone3) == 0:
                msg = f" {channel_names[channel]} - {confidence:.2f} - {person} found in zone {zone} - start recording"
                tasks.append(asyncio.ensure_future(self.trigger_zone(session, zone, True)))
            self.zone3[channel] = channel
        else:
            if len(self.zone4) == 0:
                msg = f" {channel_names[channel]} - {confidence:.2f} - {person} found in zone {zone} - start recording"
                tasks.append(asyncio.ensure_future(self.trigger_zone(session, zone, True)))
            self.zone4[channel] = channel
        return msg

    async def cleanstart(self, session, zone):
        await self.trigger_zone(session, zone, False)        

    # The main loop
    async def main(self):
        conn = aiohttp.TCPConnector(limit=None, ttl_dns_cache=300)
        async with aiohttp.ClientSession(headers=self.session_auth(), connector=conn) as session:
            # async with httpx.AsyncClient(headers=self.session_auth()) as client:
            for i in range(1, 5):
                await self.cleanstart(session, i)
            while True:
                try:
                    start = time.time()
                    tasks = []
                    channel_frames, timer = await af.get_frames(session, self.config.get('DVR', 'ip'),
                                                                self.chcnt, self.jpeg)
                    
                    for channel, frame in channel_frames:
                        # detect objects in the image
                        await self.detect(frame, self.trt_yolo, 0.65, self.vis, channel, session, tasks)
                    end = time.time()
                    await asyncio.gather(*tasks)

                    for channel in self.class_garbage_collector:
                        await self.trigger_zone(session, self.determine_zone(channel), False)
                    self.class_garbage_collector = []
                    self.network_speed.append(int(self.chcnt) / (end - start - timer))
                    # keeps the network average array nice and small
                    if len(self.network_speed) > 128:
                        for i in range(0, 65):
                            self.network_speed.pop(0)

                    logging.info(f" Inference loop - {(end - start - timer):.2f}s @ {int(self.chcnt) / (end - start - timer):.2f}fps")
                except:
                    logging.info(f" ERROR")
                    pass


if __name__ == '__main__':
    cwd = os.path.dirname(os.path.abspath(__file__))
    settings = os.path.join("../", cwd, 'settings.ini')
    # print(settings)
    config = configparser.ConfigParser()
    config.read(settings)
    logging.basicConfig(format='%(levelname)s:%(message)s', level=logging.DEBUG)
    redisDb = redis.Redis(host='localhost', port=6379, db=0)
    app = SecVisionJetson(config, redisDb)
    initworkers(app)
    loop = asyncio.get_event_loop()
    result = loop.run_until_complete(app.main())
