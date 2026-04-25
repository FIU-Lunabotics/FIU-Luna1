#define _POSIX_C_SOURCE 200809L // defines the POSIX version, most recent, to implement clock_gettime() function for millis() implementation
#include <stdint.h>
#include <stdio.h>
#include <time.h>
#include <unistd.h>
#include <inttypes.h>
#include <string.h>
#include <sys/select.h> // lets us poll stdin without blocking the state loop


enum RoverState{
  IDLE,//0
  TELEOP, //1
  AUTO//2
}; // think of enum as describing ints with names EX: RED = 0

enum RoverState currentState = IDLE;// the enum object creation, system will start in IDLE state in the setup() stage
long stateStartTime = 0;
long lastPrintTime = 0;

const long printInterval = 2500; // how often to print the remaining time in milliseconds, in this case every second
const long stateHeartbeatInterval = 500;
const char *stateRequestPath = "/tmp/rover_state_request";

// Forward declare changeState because the input helper can trigger transitions.

void changeState(enum RoverState newState);
void pollStateRequest(void);
void publishCurrentState(void);

long millis() {
  struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long)ts.tv_sec * 1000LL +
           ts.tv_nsec / 1000000LL;
}
int64_t unixMillis(void){
  struct timespec ts;
  clock_gettime(CLOCK_REALTIME, &ts);
  return (int64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

// Allow simple manual testing of the state machine from stdin.
void handleStateCommand(char command) {
  switch (command) {
    case 'i':
    case 'I':
      changeState(IDLE);
      break;
    case 't':
    case 'T':
      changeState(TELEOP);
      break;
    case 'a':
    case 'A':
      changeState(AUTO);
      break;
    default:
      break;
  }
}

// Check stdin without blocking so the main loop can keep running normally.
void pollStateInput(void) {
  fd_set readSet;
  struct timeval timeout = {0, 0};

  FD_ZERO(&readSet);
  FD_SET(STDIN_FILENO, &readSet);

  if (select(STDIN_FILENO + 1, &readSet, NULL, NULL, &timeout) <= 0) {
    return;
  }

  if (!FD_ISSET(STDIN_FILENO, &readSet)) {
    return;
  }

  char inputBuffer[32];
  ssize_t bytesRead = read(STDIN_FILENO, inputBuffer, sizeof(inputBuffer));
  if (bytesRead <= 0) {
    return;
  }

  for (ssize_t i = 0; i < bytesRead; i++) {
    handleStateCommand(inputBuffer[i]);
  }
}

void pollStateRequest(void) {
  FILE *requestFile = fopen(stateRequestPath, "r");
  if (requestFile == NULL) {
    return;
  }

  char line[128];
  if (fgets(line, sizeof(line), requestFile) != NULL) {
    char *modeToken = strtok(line, ",\n\r");
    if (modeToken != NULL) {
      if (strcmp(modeToken, "IDLE") == 0) {
        changeState(IDLE);
      } else if (strcmp(modeToken, "TELEOP") == 0) {
        changeState(TELEOP);
      } else if (strcmp(modeToken, "AUTO") == 0) {
        changeState(AUTO);
      }
    }
  }

  fclose(requestFile);
  unlink(stateRequestPath);
}

void publishCurrentState(void) {
  const char *stateName = "UNKNOWN";
  FILE *stateFile = NULL;

  switch (currentState){
    case IDLE:
      stateName = "IDLE";
      break;
    case TELEOP:
      stateName = "TELEOP";
      break;
    case AUTO:
      
      stateName = "AUTO";
      break;
  }  

  int64_t stateTimestamp = unixMillis();

  // Export the current rover mode for the Go server's Method 2 serial gate.
  stateFile = fopen("/tmp/rover_state", "w");
  if(stateFile != NULL){
    fprintf(stateFile, "%s,%" PRId64 "\n", stateName, stateTimestamp);
    fflush(stateFile);
    fclose(stateFile);
  } else {
    perror("failed to open /tmp/rover_state");

  }
}

void changeState(enum RoverState newState){ // newState is just another instance for the rover state in function that is used in this function to change the state
  long now = millis(); 
  
  currentState = newState;
  stateStartTime = now;
  lastPrintTime = now; // millis returns the number of milliseconds since the board began running the current program, stores it in stateStartTime so we know when state was entered/started so we can do countdowns based on that time.
  
  publishCurrentState();

  switch (currentState){
    case IDLE:
      printf("IDLE\n");
      fflush(stdout);
      break;
    case TELEOP:
      printf("TELEOP\n");
      fflush(stdout);
      break;
    case AUTO:
      printf("AUTO\n");
      fflush(stdout);
      break;
  }
} 

void setup() {
      changeState(IDLE);
      // Manual controls make it easy to test Method 2 without other subsystems.
      printf("State controls: i=IDLE, t=TELEOP, a=AUTO\n");
      fflush(stdout);
}

void loop() {
  long now = millis(); //how long the program has been running minus the time when the state started which atp is 0 with stateStartTime updated every time the state changes.
  static long lastStateHeartbeatTime = 0;
  // Poll once per loop so state transitions can be triggered interactively.
  pollStateInput();
  pollStateRequest();

  if (now - lastStateHeartbeatTime >= stateHeartbeatInterval){
    publishCurrentState();
    lastStateHeartbeatTime = now;
  }

  switch(currentState){
    case IDLE:
      if (now - lastPrintTime >= printInterval){
        printf("IDLE tick: %ld ms\n", now);
        fflush(stdout);
        lastPrintTime = now;}
      break;

    case TELEOP:
      break;

    case AUTO:
      break;

  }
}

int main(void){
  setup();
  while(1){loop();}
}
