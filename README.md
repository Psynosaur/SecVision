#### SecVision

Technologies used
  - Jetson Nano
 - Jetson Inference - Detectnet
 - TensorRT - Yolov4-416  
 - Networks
 - HikVision DVR

### Setup prior to use

- detect.py
  - **PLEASE DOWNLOAD THE MODELS FIRST** 
  - https://github.com/dusty-nv/jetson-inference/blob/master/docs/building-repo-2.md#downloading-models
  - SSD-MOBILENET-V2 **OR** change model hardcoded in code
- detect_yolo.py
  - **PLEASE BUILD THIS PROJECT FIRST**
  - https://github.com/jkjung-avt/tensorrt_demos
  - Environment is ready when Demo #5 runs with yolov4-416 model
    

### Goals

 - Use still frames from HTTP GET from DVR to analyze zones(cameras)
 - Detected persons
   - HTTP PUT to HikVision DVR
   - Use DVR output connected to input to trigger recording on DVR
 
 ### Usage

   Setup settings.ini
   
   On DVR set basic auth for HTTP request
   
    git clone https://github.com/Psynosaur/JetsonSecVision && cd JetsonSecVision
    pip3 install aiofiles aiohttp asyncio

   ### jetson inference stack 

    python3 detect.py

   Takes approximately 1.1 seconds to do its thing for 8x2MP images, sometimes a little longer at 1.5s when writing files
   
   ### Tensort stack with yolov4, needs symlinks to tensorrt_demo project

    python3 detect_yolo.py

   Takes approximately 2.6 seconds round trip to do its thing for 8x2MP images and is very accurate
   Detection step takes 1.75s for a network fps of **~4.57FPS**.

   ### Automatic / Continuous Operation
   
   #### Run install scripts

   ##### Jetson-Inference

    $ sudo ./install.sh
    
   ##### TensorRT-Yolov4-416 - symlinks : This assumes tensorrt_demos has been cloned in your home directory, change if needed

    ln -s ${HOME}/tensorrt_demos/utils/ ./utils
    ln -s ${HOME}/tensorrt_demos/plugins/ ./plugins
    ln -s ${HOME}/tensorrt_demos/yolo/ ./yolo

   Installation

    $ sudo ./install_yolo.sh


   ### Check status of service jetson.utils using ssd-mobilenet-v2

    $ sudo service detect status

   or yolov4-416 with openCV

    $ sudo service detect_yolo status
     
   ### To stop the service, simply run:

    $ sudo service detect stop

   or

    $ sudo service detect_yolo stop

   ### To uninstall the service

    $ sudo ./uninstall.sh

   or

    $ sudo ./uninstall_yolo.sh

### Developers

    $ sudo ./refresh.sh
 
  or

    $ sudo ./refresh_yolo.sh

