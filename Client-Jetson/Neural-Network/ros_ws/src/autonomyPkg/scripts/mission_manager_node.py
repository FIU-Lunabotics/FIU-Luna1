#!/usr/bin/env python3

import rospy
import smach
import smach_ros
from move_base_msgs.msg import MoveBaseAction

from autonomyPkg.mission_context import MissionContext
from autonomyPkg.states.idle import IdleState, CheckSystemsState
from autonomyPkg.states.exploration import ExploreState
from autonomyPkg.states.digging import DigState
from autonomyPkg.states.dumping import DumpState, CompleteState
from autonomyPkg.states.recovery import RecoveryState


def build_state_machine(context):
    sm = smach.StateMachine(outcomes=["SHUTDOWN"])

    with sm:
        smach.StateMachine.add("IDLE", IdleState(context), transitions={
            "idle": "IDLE",
            "start": "CHECK_SYSTEMS",
            "fault": "RECOVERY",
        })

        smach.StateMachine.add("CHECK_SYSTEMS", CheckSystemsState(context), transitions={
            "ready": "EXPLORE",
            "waiting": "CHECK_SYSTEMS",
            "fault": "RECOVERY",
        })

        smach.StateMachine.add("EXPLORE", ExploreState(context), transitions={
            "target_found": "TRAVEL_TO_DIG",
            "searching": "EXPLORE",
            "fault": "RECOVERY",
        })

        smach.StateMachine.add(
            "TRAVEL_TO_DIG",
            smach_ros.SimpleActionState(
                "move_base",
                MoveBaseAction,
                goal_slots=["target_pose"]
            ),
            transitions={
                "succeeded": "DIG",
                "aborted": "EXPLORE",
                "preempted": "IDLE",
            },
            remapping={
                "target_pose": "dig_site_goal"
            }
        )

        smach.StateMachine.add("DIG", DigState(context), transitions={
            "dug": "TRAVEL_TO_BERM",
            "timeout": "RECOVERY",
            "fault": "RECOVERY",
        })

        smach.StateMachine.add(
            "TRAVEL_TO_BERM",
            smach_ros.SimpleActionState(
                "move_base",
                MoveBaseAction,
                goal_slots=["target_pose"]
            ),
            transitions={
                "succeeded": "DUMP",
                "aborted": "EXPLORE",
                "preempted": "IDLE",
            },
            remapping={
                "target_pose": "berm_goal"
            }
        )

        smach.StateMachine.add("DUMP", DumpState(context), transitions={
            "dumped": "EXPLORE",
            "complete": "COMPLETE",
            "timeout": "RECOVERY",
            "fault": "RECOVERY",
        })

        smach.StateMachine.add("COMPLETE", CompleteState(context), transitions={
            "done": "SHUTDOWN",
        })

        smach.StateMachine.add("RECOVERY", RecoveryState(context), transitions={
            "recovered": "CHECK_SYSTEMS",
            "fatal": "SHUTDOWN",
        })

    return sm


def main():
    rospy.init_node("luna_autonomy_state_machine")

    context = MissionContext()
    state_machine = build_state_machine(context)

    introspection_server = smach_ros.IntrospectionServer(
        "luna_autonomy_smach",
        state_machine,
        "/LUNA_AUTONOMY"
    )
    introspection_server.start()

    try:
        outcome = state_machine.execute()
        rospy.loginfo("Luna autonomy state machine finished with outcome: %s", outcome)
    finally:
        introspection_server.stop()


if __name__ == "__main__":
    main()
