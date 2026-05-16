#!/usr/bin/env python3
import rospy
import smach
import smach_ros
from move_base_msgs.msg import MoveBaseAction

# 1. Import your custom library logic
from state_machine_pkg.mission_context import MissionContext

# 2. Import your isolated states (these will be the files you build next)
from state_machine_pkg.states.idle import IdleState, CheckSystemsState
from state_machine_pkg.states.exploration import ExploreState
from state_machine_pkg.states.digging import DigState
from state_machine_pkg.states.dumping import DumpState, CompleteState
from state_machine_pkg.states.recovery import RecoveryState

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

        # --- THE NEW NAVIGATION STACK IMPLEMENTATION ---
        smach.StateMachine.add("TRAVEL_TO_DIG", 
            smach_ros.SimpleActionState('move_base', MoveBaseAction, goal_slots=['target_pose']),
            transitions={'succeeded':'DIG', 'aborted':'EXPLORE', 'preempted':'IDLE'},
            remapping={'target_pose':'dig_site_goal'})

        smach.StateMachine.add("DIG", DigState(context), transitions={
            "dug": "TRAVEL_TO_BERM",
            "timeout": "RECOVERY",
            "fault": "RECOVERY",
        })

        # --- THE NEW NAVIGATION STACK IMPLEMENTATION ---
        smach.StateMachine.add("TRAVEL_TO_BERM", 
            smach_ros.SimpleActionState('move_base', MoveBaseAction, goal_slots=['target_pose']),
            transitions={'succeeded':'DUMP', 'aborted':'EXPLORE', 'preempted':'IDLE'},
            remapping={'target_pose':'berm_goal'})

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

    # Starts the web/GUI introspection server so you can visually see the states
    introspection_server = smach_ros.IntrospectionServer(
        "luna_autonomy_smach", state_machine, "/LUNA_AUTONOMY"
    )
    introspection_server.start()

    try:
        outcome = state_machine.execute()
        rospy.loginfo("Luna_autonomy state machine finished with outcome: %s", outcome)
    finally:
        introspection_server.stop()

if __name__ == "__main__":
    main()