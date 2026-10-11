TARGET := build/robot
CC := arm-none-eabi-gcc
OBJCOPY := arm-none-eabi-objcopy
SIZE := arm-none-eabi-size
HOST_CC ?= cc
SANITIZE ?=
ARCH := -mcpu=cortex-m4 -mthumb -mfpu=fpv4-sp-d16 -mfloat-abi=hard
WARN := -Wall -Wextra -Werror -Wshadow -Wconversion -Wdouble-promotion
CFLAGS := $(ARCH) -std=c11 -Os -g3 $(WARN) -ffunction-sections -fdata-sections -Iinclude -MMD -MP
LDFLAGS := $(ARCH) -nostartfiles --specs=nosys.specs -Tfirmware/stm32f446re.ld -Wl,--gc-sections,-z,noexecstack,-Map=$(TARGET).map
SOURCES := firmware/main.c firmware/board.c firmware/startup.c firmware/syscalls.c src/kinematics.c src/motion.c src/controller.c
OBJECTS := $(patsubst %.c,build/%.o,$(SOURCES)) build/firmware/vectors.o

.PHONY: all flash probe test clean
all: $(TARGET).elf $(TARGET).bin

$(TARGET).elf: $(OBJECTS) firmware/stm32f446re.ld Makefile
	$(CC) $(LDFLAGS) $(OBJECTS) -lm -lc -lgcc -o $@
	$(SIZE) $@

$(TARGET).bin: $(TARGET).elf
	$(OBJCOPY) -O binary $< $@

build/%.o: %.c Makefile
	@mkdir -p $(@D)
	$(CC) $(CFLAGS) -c $< -o $@

build/firmware/vectors.o: firmware/vectors.S Makefile
	@mkdir -p $(@D)
	$(CC) $(ARCH) -g3 -c $< -o $@

# Use the Nucleo's onboard ST-LINK; preserve SWD pins PA13/PA14.
probe:
	openocd -f interface/stlink.cfg -f target/stm32f4x.cfg -c "init; shutdown"

flash: all
	openocd -f interface/stlink.cfg -f target/stm32f4x.cfg -c "program $(TARGET).elf verify reset exit"

build/test_robot: tests/test_robot.c src/kinematics.c src/motion.c src/controller.c include/kinematics.h include/motion.h include/controller.h Makefile
	@mkdir -p build
	$(HOST_CC) -std=c11 -O1 -g $(WARN) -ftrapv $(SANITIZE) -Iinclude tests/test_robot.c src/kinematics.c src/motion.c src/controller.c -lm -o $@

test: build/test_robot
	./build/test_robot
	python3 -m unittest discover -s tests -p 'test_*.py'

clean:
	$(RM) -r build

-include $(OBJECTS:.o=.d)
