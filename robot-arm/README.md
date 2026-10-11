# STM32 Nucleo F446RE planar robot

Firmware for two serial rotary joints: a shoulder motor drives arm 1; an elbow
motor mounted on arm 1 drives arm 2. A laptop sends XY coordinates over the
Nucleo's ST-LINK virtual serial port. No external libraries or IDE are required.

This folder is self-contained. From the Audit repository root:

```bash
make -C robot-arm
make -C robot-arm test
make -C robot-arm flash
python3 robot-arm/tools/arrow_control.py
```

Commands below are run from inside `robot-arm/`. The Audit website sends motion
through [`backend/robot_serial.py`](../backend/robot_serial.py), which waits for
move completion before oscilloscope capture. Configure the serial port and pin
coordinates as described in [`backend/README.md`](../backend/README.md), and set
the arm's measured geometry, known pose, and ARM state before automatic jobs.

## Coordinates and starting pose

- Origin: shoulder shaft center.
- `L1`: shoulder shaft center to elbow shaft center, in millimeters.
- `L2`: elbow shaft center to tool point, in millimeters.
- Shoulder angle `Q1`: arm 1 relative to +X.
- Elbow angle `Q2`: arm 2 relative to arm 1, **not** relative to the room.
- Positive angles: counterclockwise when looking down on the XY plane.
- Your starting pose is `POSE 0 90`: arm 1 points along +X and arm 2 along +Y.
  Its tool position is `(L1, L2)`.

The shoulder rotation carries the elbow and arm 2 with it. Forward kinematics:

```text
x = L1 cos(Q1) + L2 cos(Q1 + Q2)
y = L1 sin(Q1) + L2 sin(Q1 + Q2)
```

The controller solves the inverse equations for a requested point, preserves the
elbow branch selected by `POSE`, checks joint limits, and rounds the joint targets
to whole driver pulses. It does not automatically switch elbow orientations.

`MOVE` interpolates **joint positions** with coordinated stepping and a master
acceleration/deceleration profile. Both joints finish together. The tool follows
a curve between points; this version does not promise a straight Cartesian path,
constant Cartesian speed, obstacle avoidance, or encoder feedback.

## Build and flash

Tools: the ARM GCC toolchain (`arm-none-eabi-gcc`, binutils, and newlib), GNU Make,
OpenOCD, Python 3, and a native C compiler for laptop tests. The serial tools use
only the Python standard library on Linux/macOS.

```bash
make -j2
make test
make probe
make flash
```

Outputs: `build/robot.elf`, `build/robot.bin`, and `build/robot.map`.
`make flash` programs and verifies the ELF through the onboard ST-LINK, then resets
the MCU to run it. The firmware starts **unconfigured and disarmed**, with STEP
outputs low. XY moves require configuration, a known pose, and `ARM`. Single-motor
bench jogging uses explicit `MOTORARM` and `JOG` commands without geometry.

The bare-metal board layer uses the internal 16 MHz HSI oscillator, the Cortex-M4
FPU, USART2, TIM2, and SysTick. PA13/PA14 remain available for SWD programming.
There is no dependency on the ST-LINK clock output or a CubeMX-generated project.

## Pin assignment

| Function | STM32 pin | Arduino header label |
| --- | --- | --- |
| Shoulder STEP / PULSE− | PB3 | D3 |
| Shoulder DIR / DIR− | PB5 | D4 |
| Elbow STEP / PULSE− | PC7 | D9 |
| Elbow DIR / DIR− | PA9 | D8 |
| Serial TX to ST-LINK | PA2 | Internally connected by default |
| Serial RX from ST-LINK | PA3 | Internally connected by default |
| Status LED | PA5 | LD2 / D13 |
| Stop input | PC13 | Blue user button B1 |

The driver must use pulse/direction mode. A STEP pulse is HIGH for one 50 us timer
tick, followed by at least one LOW tick. Direction is established at least 100 us
before the first pulse. `INVERT` changes the direction of each joint individually.
The firmware does not control the driver's ENA terminals: ARM/DISARM gate STEP
generation and do not switch motor holding current.

The LED blinks when disarmed and stays lit when armed. Pressing B1 for 5 ms stops
pulse generation and invalidates the pose reference. `STOP`, `!`, and serial
overrun/framing errors also stop motion. These software stops do not disconnect
driver power.

## Connect from the laptop

Use 115200 baud, 8 data bits, no parity, 1 stop bit, no flow control. Default
Nucleo solder bridges connect USART2 to the onboard ST-LINK virtual COM port.

```bash
python3 tools/robot_serial.py --list
python3 tools/robot_serial.py --port /dev/ttyACM0
```

The supplied terminal uses the Python standard library on Linux/macOS. It
automatically selects the port if exactly one is detected. A serial terminal
on another OS can send the same commands. Commands are uppercase, one per line;
the supplied terminal converts typed commands to uppercase.

## Arrow control for one or both motors

Motor 1 uses **D3 = PULSE−**, **D4 = DIR−**. Motor 2 uses **D9 = PULSE−**,
**D8 = DIR−**. Both pulse pins stay LOW while idle, disarmed, stopped, or in the
fault handler. The default pulse rate is
**100 pulses/second**, half the former 200 pulses/second default.

From an interactive terminal:

```bash
python3 tools/arrow_control.py
```

The tool opens the serial connection, selects `SPEED 100 400`, and enables
bench jogging with `MOTORARM`. Press **1** or **2** to select one motor, or **3**
to select both. Left/right sends `JOG <selection> -10` / `JOG <selection> 10`.
In selection 3, both motors receive the same signed pulse count from one
coordinated move, starting and finishing together. Their pulse outputs switch on
the same timer tick. Equal angular motion also requires equal pulse settings and
gearing. Selecting a mode while moving applies it to the next jog.
**Up** increases speed by 25 pulses/s; **Down** decreases it by 25 pulses/s, within
1–4000 pulses/s. The terminal shows the selected speed. A change during a jog is
applied when that jog finishes, before the next move. Acceleration scales with
speed so changes also affect these short jogs, within firmware acceleration limits.
Hold the key to repeat. It sends one motion at a time and drops keyboard repeats
while busy, so releasing the key cannot leave a long queue of motion. Space,
`!`, Q, or Ctrl-C stops the motor and closes the controller. Start the tool again
to resume after a stop.

Options: `--port /dev/ttyACM0`, `--motor 3` (both), `--steps 20`, `--speed 100`,
`--speed-step 25`. Jog distances are
**driver pulses**, not degrees; the actual angle depends on the microstep setting.
No arm dimensions, microstep calibration, or pose are required for this motor
test. It leaves the unselected motor's pulse count unchanged. It invalidates any
previous robot pose; use CONFIG and POSE again to return to XY control.

Start with `HELP` and `STATUS`. Enter your measured dimensions and actual driver
pulse settings, then physically place the arm at its right-angle starting pose
and declare `POSE 0 90`. `POSE` records the current physical pose; it never moves
the motors to that pose.

Example with **illustrative values**, not your measured robot configuration:

```text
CONFIG 200 150 3000 3000
LIMITS -170 170 -170 170
SPEED 200 400
POSE 0 90
CHECK 200 150
ARM
JOINT 1 90
STATUS
```

The example uses arm lengths 200 mm and 150 mm and 3000 pulses per joint
revolution. Replace all four CONFIG values with your real measurements/settings.
Wait for `EVENT DONE` before sending the next motion command. A one-degree
`JOINT` move helps verify direction and scale during initial calibration.

To send commands from a script:

```bash
python3 tools/robot_serial.py --port /dev/ttyACM0 --command STATUS
python3 tools/robot_serial.py --port /dev/ttyACM0 --command 'MOVE 180 160' --wait --timeout 60
```

The second example assumes the board was already configured, referenced, and
armed. No setup values or arm command are sent automatically.

## Pulse calibration

The 573S15-L is a three-phase motor with a **1.2 degree native step angle**, so it
has 300 full steps per motor revolution. Use the driver's actual **pulses per
revolution** setting rather than assuming a typical 200-step motor or interpreting
a DIP label as a microstep multiplier.

```text
PPR_joint = driver pulses per motor revolution × motor revolutions per joint revolution
```

For a direct drive, the gear ratio is 1. If the driver is actually set to 3000
pulses/revolution, a direct-drive arm uses `PPR = 3000`. If it is set to a 10x
native microstep multiplier, `PPR = 300 × 10 = 3000`. If it is labeled directly
in pulses/revolution, use that value as-is. Gear reductions multiply PPR by the
reduction ratio. Both joints can have different pulse settings or gearing.

Position estimates come from emitted pulses. Set the known pose again after a
reset, missed steps, power loss, or manual repositioning. Configuration is kept
in RAM and must be resent after reset; firmware does not wear flash by saving
every command.

## Serial commands

| Command | Action |
| --- | --- |
| `HELP` | List commands |
| `STATUS` | State, estimated angles/XY, geometry, PPR, speed and direction settings |
| `CONFIG L1 L2 PPR1 PPR2` | Set lengths in mm and pulses per joint revolution; disarmed only; invalidates reference |
| `LIMITS MIN1 MAX1 MIN2 MAX2` | Set joint angle limits in degrees; disarmed only |
| `INVERT 0 1` | Example: reverse elbow DIR polarity; disarmed only; invalidates reference |
| `SPEED RATE ACCEL` | Master pulse rate in pulses/s and acceleration in pulses/s² |
| `POSE Q1 Q2` | Declare the current known pose without moving; disarmed only |
| `ARM` | Permit motion after CONFIG and POSE |
| `CHECK X Y` | Calculate an XY target's joint angles without moving |
| `MOVE X Y` | Move to a point in mm |
| `HOME` | Return to the pose last declared with POSE; requires geometry, reference, and ARM |
| `JOINT Q1 Q2` | Move to absolute joint angles in degrees; also selects the elbow branch for future XY moves |
| `MOTORARM` | Enable bench jogging without geometry; invalidates the XY pose |
| `JOG PULSES` | Relative motor 1 motion; integer -2000 to +2000; MOTORARM required |
| `JOG MOTOR PULSES` | Select 1 or 2 for one motor, or 3 for both with the same signed pulse count |
| `DISARM` | Gate pulses; if moving, stop and invalidate the reference |
| `STOP` | Stop immediately and invalidate the reference |
| `!` | Immediate stop character, no newline required |

Replies start with `OK` or `ERR`; asynchronous notifications start with `EVENT`.
A move replies `OK MOVING ...`, then `EVENT DONE` after its last pulse ends.
If no step is required, it replies `OK DONE ...`. `ERR busy` rejects extra motion
and configuration commands while moving; there is no hidden command queue.

Default limits are -170° to +170° for both joints. Set actual mechanical travel
limits before operating the robot. Rate defaults to 100 pulses/s, acceleration to
400 pulses/s². Rate is limited to 4000 pulses/s; acceleration accepts 1–100000
pulses/s². Each move is limited to one million master steps. Invalid, nonfinite,
overlong, unreachable, and out-of-limit commands are rejected.

## Validation and source layout

`make test` checks 1312 forward/inverse round trips across both elbow branches,
workspace boundaries and singularities, joint limits and angle wrapping,
coordinated pulse counts and timing, reversals, stop/restart, quantized limits,
command parsing/state transitions, and the laptop terminal using simulated serial
devices. Native signed integer overflow is trapped; additional sanitizer flags
can be supplied through `SANITIZE` when that runtime is installed.

- `src/kinematics.c`: robot geometry and inverse kinematics.
- `src/motion.c`: timer-driven pulse planner, also exercised on the laptop.
- `src/controller.c`: serial commands and calibration state.
- `firmware/board.c`: clock, pins, UART and timers.
- `firmware/main.c`: interrupt/main-loop integration; parsing and formatting do
  not block motor interrupts.
- `tools/robot_serial.py`: serial terminal and scriptable command client.
- `tools/arrow_control.py`: motor selection, left/right jogging and up/down speed control.
- `tests/`: robot math/control tests and simulated serial tests.

References: [ST Nucleo user manual](https://www.st.com/resource/en/user_manual/dm00105823.pdf),
[ST F446 register definitions](https://github.com/STMicroelectronics/cmsis-device-f4/blob/master/Include/stm32f446xx.h),
[573S15-L motor datasheet](https://www.leadshine.com/upfiles/downloads/b382ed189ef6a9fa98cb356c900a771a_1652149166879.pdf),
[OpenOCD programming documentation](https://openocd.org/doc/html/Flash-Programming.html).
