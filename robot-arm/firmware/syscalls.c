#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#include <sys/stat.h>
#include <sys/types.h>

#include "board.h"

extern char _end, _stack_limit;

/* newlib's number parsing/formatting uses a small heap. */
void *_sbrk(ptrdiff_t increment)
{
    static uintptr_t heap;
    if (heap == 0) {
        heap = (uintptr_t)&_end;
    }
    if (increment < 0 || (uintptr_t)increment > (uintptr_t)&_stack_limit - heap) {
        errno = ENOMEM;
        return (void *)-1;
    }
    const uintptr_t previous = heap;
    heap += (uintptr_t)increment;
    return (void *)previous;
}

int _close(int fd) { (void)fd; errno = EBADF; return -1; }
int _fstat(int fd, struct stat *status)
{
    (void)fd;
    status->st_mode = S_IFCHR;
    return 0;
}
int _isatty(int fd) { return fd >= 0 && fd <= 2; }
int _getpid(void) { return 1; }
int _kill(int pid, int signal) { (void)pid; (void)signal; errno = ENOSYS; return -1; }
off_t _lseek(int fd, off_t offset, int whence)
{
    (void)fd; (void)offset; (void)whence; errno = ESPIPE; return (off_t)-1;
}
int _read(int fd, void *buffer, size_t length)
{
    (void)fd; (void)buffer; (void)length; errno = ENOSYS; return -1;
}
int _write(int fd, const void *buffer, size_t length)
{
    (void)fd;
    const char *data = buffer;
    for (size_t i = 0; i < length; ++i) {
        const char character[2] = {data[i], '\0'};
        board_serial_write(character);
    }
    return (int)length;
}
__attribute__((noreturn)) void _exit(int status)
{
    (void)status;
    __asm volatile("cpsid i" ::: "memory");
    board_step_levels(0);
    for (;;) { __asm volatile("wfi"); }
}
