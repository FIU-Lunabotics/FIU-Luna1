````markdown
# Camera–LiDAR Sensor Fusion Workflow Using ROS Noetic and cam2lidar

This guide explains how to perform sensor fusion between a camera and a LiDAR using ROS Noetic and the `cam2lidar` package. The objective is to spatially align both sensors so that LiDAR points can be projected accurately onto the camera image.

Once calibration is complete, the robot can combine:

- visual information from the camera
- spatial geometry from the LiDAR
- motion data from the IMU

This forms the foundation for:

- SLAM
- visual odometry
- LiDAR odometry
- obstacle detection
- terrain mapping
- autonomous navigation
- Lunabotics rover autonomy

---

# Complete Workflow Overview

```text
Camera publishes image
        +
LiDAR publishes point cloud
        +
Verify ROS topics
        +
Camera intrinsic calibration
        +
Build calibration board
        +
Install cam2lidar
        +
Configure calibration parameters
        +
Run geometric calibration
        +
Verify projection in RViz
        +
Save static transform
````

---

# Understanding the Three Types of Calibration

Before beginning implementation, it is important to understand the three calibration stages used in robotics sensor fusion systems.

---

# 1. Intrinsic Calibration

Intrinsic calibration computes the internal optical properties of the camera itself.

It answers:

```text
How does this camera see the world?
```

This computes:

* focal length
* optical center
* lens distortion
* image geometry

Intrinsic calibration affects only the camera and does not involve the LiDAR.

Without intrinsic calibration, LiDAR points cannot align correctly with the camera image.

---

# 2. Geometric Calibration

Geometric calibration computes the spatial relationship between the camera and the LiDAR.

It answers:

```text
Where is the camera relative to the LiDAR?
```

This computes:

* translation `(x, y, z)`
* rotation `(roll, pitch, yaw)`

The final output is a transformation matrix between both sensors.

This is the primary purpose of the `cam2lidar` package.

---

# 3. Temporal Calibration

Temporal calibration synchronizes sensor timing.

It answers:

```text
Are both sensors capturing data at the same time?
```

Even small timing offsets can create major projection errors while the robot moves.

Example:

```text
Camera frame captured at:
12.000 seconds

LiDAR scan captured at:
12.100 seconds
```

If the rover moves during those 100 milliseconds, both sensors no longer agree spatially.

Temporal calibration compensates for these timing mismatches.

---

# Step 1 — Install Required ROS Packages

Install all required ROS packages.

```bash
sudo apt update

sudo apt install \
ros-noetic-rviz \
ros-noetic-tf \
ros-noetic-image-view \
ros-noetic-camera-calibration \
ros-noetic-pcl-ros \
ros-noetic-cv-bridge \
ros-noetic-image-transport
```

---

# Package Explanation

| Package              | Purpose                                       |
| -------------------- | --------------------------------------------- |
| `rviz`               | Visualize point clouds, images, and TF frames |
| `tf`                 | Coordinate frame transformations              |
| `image-view`         | Display camera images                         |
| `camera-calibration` | Camera intrinsic calibration                  |
| `pcl-ros`            | Point cloud processing                        |
| `cv-bridge`          | ROS ↔ OpenCV conversion                      |
| `image-transport`    | Optimized image communication                 |

These packages form the base of the sensor fusion pipeline.

---

# Step 2 — Verify Sensor ROS Topics

Launch both the camera and LiDAR drivers.

Then verify topics:

```bash
rostopic list
```

Expected camera topics:

```text
/image_raw
/camera_info
```

Expected LiDAR topics:

```text
/points_raw
/lidar_points
```

Explanation:

* `/image_raw` contains live camera images
* `/camera_info` contains camera calibration parameters
* `/points_raw` contains LiDAR point clouds

At this stage:

* the camera continuously publishes image frames
* the LiDAR continuously publishes 3D spatial data

---

# Step 3 — Verify Camera Feed

Launch the image viewer:

```bash
rqt_image_view
```

Select:

```text
/image_raw
```

The live camera feed should appear.

This verifies:

* ROS image transport works
* the camera node publishes correctly
* the camera stream is accessible

---

# Step 4 — Verify LiDAR Point Cloud

Launch RViz:

```bash
rviz
```

Inside RViz:

1. Click `Add`
2. Select `PointCloud2`
3. Choose topic:

```text
/points_raw
```

You should now see the LiDAR point cloud.

This confirms:

* LiDAR communication works
* ROS receives point cloud data correctly

---

# Step 5 — Perform Camera Intrinsic Calibration

Before calibrating against the LiDAR, the camera must first be calibrated internally.

A checkerboard calibration target is used because checkerboard corners are easy to detect accurately.

---

# Build the Checkerboard

Recommended configuration:

```text
Inner corners: 8 × 6
Square size: 2.5 cm
```

Recommended materials:

* foam board
* rigid cardboard
* matte paper print

The board must remain perfectly flat.

---

# Run Intrinsic Calibration

```bash (Update measurements)
rosrun camera_calibration cameracalibrator.py \
--size 8x6 \
--square 0.025 \
image:=/image_raw \
camera:=/camera
```

---

# Parameter Explanation

| Parameter           | Meaning                    |
| ------------------- | -------------------------- |
| `--size 8x6`        | Checkerboard inner corners |
| `--square 0.025`    | Square size in meters      |
| `image:=/image_raw` | Camera image topic         |
| `camera:=/camera`   | Camera namespace           |

---

# Intrinsic Calibration Procedure

Move the checkerboard:

* left
* right
* closer
* farther
* tilted
* rotated

The software collects checkerboard corner observations across the image.

This allows ROS to estimate:

* lens distortion
* focal length
* optical center

After enough samples:

1. Click:

   ```text
   CALIBRATE
   ```

2. Then click:

   ```text
   SAVE
   ```

ROS will now publish:

```text
/camera_info
```

This topic contains the camera intrinsic calibration parameters.

---

# Step 6 — Build the cam2lidar Calibration Board

The geometric calibration board must be visible to BOTH sensors.

The board combines:

* AprilTag for camera detection
* reflective tape for LiDAR intensity detection

---

# Recommended Board Layout

```text
+----------------------------------+
|  REFLECTIVE TAPE BORDER          |
|  ##############################  |
|  #                            #  |
|  #        APRILTAG            #  |
|  #       (centered)           #  |
|  #                            #  |
|  ##############################  |
|  REFLECTIVE TAPE BORDER          |
+----------------------------------+
```

---

# Recommended Dimensions

```text
Ideally:
Board size: A3 preferred
AprilTag size: 10 cm × 10 cm
Reflective tape width: 2–3 cm
```

---

# Recommended Materials

| Material               | Purpose                   |
| ---------------------- | ------------------------- |
| cardboardboard         | Rigid flat surface        |
| Printed AprilTag       | Camera detection          |
| Retroreflective tape   | LiDAR intensity detection |
| Glue or spray adhesive | Stable mounting           |

---

# Important Notes About Reflective Tape

The LiDAR detects:

* distance
* reflection intensity

The reflective tape must be:

* retroreflective
* high intensity
* smooth
* wrinkle-free

Color is less important than reflectivity.

Good choices:

* silver reflective tape
* white reflective tape
* industrial safety reflective tape

Red reflective tape works ONLY if it is truly retroreflective.

---

# Step 7 — Install cam2lidar

Clone the repository:

```bash
cd ~/catkin_ws/src

git clone https://github.com/up2metric/cam2lidar.git
```

Build the workspace:

```bash
cd ~/catkin_ws

catkin_make
```

Source the workspace:

```bash
source devel/setup.bash
```

---

# Step 8 — GUI Permissions (Important)

Some systems require GUI display permissions for RViz and OpenCV windows.

Run:

```bash
xhost +local:
```

This allows local GUI applications to display correctly.

This is commonly needed when:

* using Docker
* using remote sessions
* GUI windows fail to open

If everything already launches correctly, this step may not be necessary.

---

# Step 9 — Verify Topics Before Geometric Calibration

Verify all required topics:

```bash
rostopic list
```

Expected topics:

```text
/image_raw
/camera_info
/points_raw
/tf
```

At this stage:

* camera images exist
* LiDAR point clouds exist
* TF transformations exist
* intrinsic calibration exists

The system is now ready for geometric calibration.

---

# Step 10 — Configure cam2lidar Parameters

The `cam2lidar` package requires calibration tuning parameters.

Example configuration:

```yaml
# Geometric calibration

reproj_error: 8
intensity_thres: 150
distance_from_prev: 100

horizontal_dimension: 1280
vertical_dimension: 720

grid_horizontal_division: 5
grid_vertical_division: 5
```

---

# Parameter Explanation

---

## reproj_error

```yaml
reproj_error: 8
```

Maximum acceptable reprojection error in pixels.

Lower values:

* higher accuracy
* stricter calibration

Higher values:

* easier calibration
* lower precision

---

## intensity_thres

```yaml
intensity_thres: 150
```

Minimum LiDAR reflection intensity.

This filters out weak reflections and isolates reflective tape returns.

If threshold is too high:

* tape may not be detected

If threshold is too low:

* noise enters calibration

---

## distance_from_prev

```yaml
distance_from_prev: 100
```

Minimum movement required before accepting another calibration sample.

This prevents duplicate viewpoints.

Calibration requires:

* varied angles
* varied distances
* varied positions

---

## horizontal_dimension

## vertical_dimension

```yaml
horizontal_dimension: 1280
vertical_dimension: 720
```

Camera image resolution.

These MUST match the actual camera output resolution.

Verify using:

```bash
rostopic echo /camera_info
```

Look for:

```text
width:
height:
```

Wrong resolution settings can severely degrade calibration quality.

---

## grid_horizontal_division

## grid_vertical_division

```yaml
grid_horizontal_division: 5
grid_vertical_division: 5
```

The image is divided into regions.

The software attempts to collect samples across:

* corners
* edges
* center

This improves geometric stability and distortion estimation.

---

# Step 11 — Run Geometric Calibration

Launch geometric calibration:

```bash
roslaunch cam2lidar geometric.launch
```

During calibration:

1. The camera detects the AprilTag
2. The LiDAR detects reflective tape
3. Both observations are matched
4. A transformation matrix is computed

---

# Calibration Procedure

Move the board:

* left/right
* up/down
* near/far
* rotated
* tilted

Move slowly and steadily.

The software requires multiple viewpoints to solve the transform robustly.

---

# What cam2lidar Is Doing Internally

The package:

1. detects the calibration target in the image
2. detects reflective points in the LiDAR point cloud
3. matches both observations geometrically
4. solves the transformation matrix

Final output:

```text
camera ↔ lidar transform
```

---

# Step 12 — Verify Calibration in RViz

Launch RViz:

```bash
rviz
```

Add:

* Camera
* PointCloud2
* TF

A successful calibration means:

```text
LiDAR points align correctly with object edges in the image.
```

Examples:

* walls align
* tables align
* obstacles align

If points appear shifted or rotated incorrectly:

* recalibration is required

---

# Step 13 — Save Static Transform

Once calibration succeeds, save the transform.

Example:

```bash
rosrun tf static_transform_publisher \
0.1 0.0 0.05 0 0 0 \
camera_frame lidar_frame 100
```

---

# Transform Explanation

```text
x y z roll pitch yaw
```

This permanently defines the spatial relationship between:

* camera frame
* LiDAR frame

ROS will continuously use this transform for:

* sensor fusion
* odometry
* SLAM
* localization
* navigation

---

# Final Result

After completing calibration:

* the camera and LiDAR become spatially aligned
* LiDAR points project correctly onto the image
* the rover can combine geometry and visual information
* sensor fusion becomes operational

This creates the foundation for:

* visual-LiDAR odometry
* autonomous navigation
* terrain mapping
* obstacle avoidance
* Lunabotics rover autonomy

```
```

