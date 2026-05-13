#!/usr/bin/env python3

import json
import sys
from pathlib import Path

import rospy
from std_msgs.msg import String


def default_cv_code_dir():
    """
    Expected repo structure:

    Client-Jetson/ComputerVision/
        cv_branch_capture.py
        cv_model_inference.py
        best.pt

    This node lives in:
    Client-Jetson/ComputerVision/ros_ws/src/rover_vision/scripts/
    """
    return Path(__file__).resolve().parents[4]


def load_cv_modules(cv_code_dir):
    cv_code_dir = Path(cv_code_dir).expanduser().resolve()

    if str(cv_code_dir) not in sys.path:
        sys.path.insert(0, str(cv_code_dir))

    from cv_branch_capture import (
        CVBranchConfig,
        retrieve_camera_stream,
        retrieve_single_frame,
    )

    from cv_model_inference import (
        load_model,
        detect_objects_in_frame,
    )

    return (
        CVBranchConfig,
        retrieve_camera_stream,
        retrieve_single_frame,
        load_model,
        detect_objects_in_frame,
    )


def build_config_from_ros_params(CVBranchConfig):
    socket_path = rospy.get_param("~socket_path", None)
    width = rospy.get_param("~width", None)
    height = rospy.get_param("~height", None)
    fps = rospy.get_param("~fps", None)

    if socket_path is None and width is None and height is None and fps is None:
        return None

    default_config = CVBranchConfig()

    return CVBranchConfig(
        socket_path=socket_path or default_config.socket_path,
        width=int(width or default_config.width),
        height=int(height or default_config.height),
        fps=int(fps or default_config.fps),
    )


def main():
    rospy.init_node("cv_detection_node")

    detections_pub = rospy.Publisher(
        "/vision/detections",
        String,
        queue_size=10
    )

    cv_code_dir_param = rospy.get_param("~cv_code_dir", "")
    cv_code_dir = (
        Path(cv_code_dir_param).expanduser()
        if cv_code_dir_param
        else default_cv_code_dir()
    )

    model_path = rospy.get_param("~model_path", str(cv_code_dir / "best.pt"))
    confidence = float(rospy.get_param("~confidence", 0.50))
    timeout = float(rospy.get_param("~timeout", 2.0))
    publish_rate = float(rospy.get_param("~publish_rate", 5.0))

    rospy.loginfo("Starting CV detection node")
    rospy.loginfo("CV code directory: %s", cv_code_dir)
    rospy.loginfo("Model path: %s", model_path)
    rospy.loginfo("Confidence threshold: %.2f", confidence)

    try:
        (
            CVBranchConfig,
            retrieve_camera_stream,
            retrieve_single_frame,
            load_model,
            detect_objects_in_frame,
        ) = load_cv_modules(cv_code_dir)

        model = load_model(model_path)
        config = build_config_from_ros_params(CVBranchConfig)
        rate = rospy.Rate(publish_rate)

        with retrieve_camera_stream(config) as stream:
            rospy.loginfo("Connected to CV camera branch")

            while not rospy.is_shutdown():
                try:
                    frame = retrieve_single_frame(stream, timeout=timeout)

                    detections = detect_objects_in_frame(
                        model=model,
                        frame=frame,
                        confidence=confidence,
                    )

                    output = {
                        "stamp": rospy.Time.now().to_sec(),
                        "detections": detections,
                    }

                    msg = String()
                    msg.data = json.dumps(output)

                    detections_pub.publish(msg)
                    rospy.loginfo("Published %d detection(s)", len(detections))

                except TimeoutError as error:
                    rospy.logwarn("Timed out waiting for CV frame: %s", error)

                except Exception as error:
                    rospy.logerr("CV detection error: %s", error)

                rate.sleep()

    except Exception as error:
        rospy.logerr("Failed to start CV detection node: %s", error)


if __name__ == "__main__":
    main()
