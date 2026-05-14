# ROS Package Dissection

The following is the base file tree of a ROS Package at its core:

examplePkg
|- include/
|- msg/
|- scripts/
|- src/
|- srv/
|- CHANGELOG.rst
|- CMakeLists.txt
|_ package.xml

## What Does Each Component do?

> `include/`

> include is a directory that stores and C/C++ header fue that defines global variables that are used accross multiple nodes/packages. We dont/probably wont need to touch that. Your included libraries instead will be declared in the CMake File

> `msg/`

> msg is the directory where you will declare the message format for the topic that the node will eventually publish 

> `scripts`

> This folder includes either the py or cpp scripts used to interface with the ros api using either rospy (the python lib for ros) or roscpp (sae for c++). You will be calling on the functions declared in the src file within this node file and then use the ros library to set publishing and subription.

>`src`

> This folder is where you put your source code either in c/c++ or python. When using a python script as source code, within the src dir you must make a sub-dir with the name of the package it is apart of, for example given package name `examplePkg`, the path to script `exampleScript.py` within this dir would look as follows:

> `examplePkg/src/examplePkg/exampleScript.py'

> The scripts you use from other languages function just as they would by running the same script in regular python/(c/c++) and you can use whatever external dependency neccesary but it must be included in the cmake file. Lastly, the only other distinguishing factor is that the source script cannot execute code itself, it should only be used to declare the functions that will be called by the node script which will pipe that data out to a ROS topic and can also get input from other topcis to use in processing data within that node and the functions defined in the src code.

> `srv`

> srv isi used for push/request type api call to request/push soecific flags like reseting a map, enabling motors, disabling actuators, etc. msg and scripts are used to declare and use topics on a constant time bases but service is used only for once in a while calls/requests to/from a node in order to have specific functionality ran in that single instance.

> `CHANGELOG`

> This is the change log for the package

> `CMakeLists.txt`

> This file declares all the dependencies and how to make the package into an executable node

> `package`

> Lastly, this xml file describes key features about the package. 
