"""Answer only the explicitly selected, actually displayed prompts in one owned tty."""
import json
import os
import pty
import re
import signal
import sys

pending = [(re.compile(pattern), answer.encode()) for pattern, answer in json.loads(sys.argv[1])]
expected = len(pending)
buffer = ""


def answer_shown_prompts(descriptor):
    global buffer
    data = os.read(descriptor, 65536)
    buffer = (buffer + data.decode("utf-8", "replace"))[-65536:]
    for prompt, answer in pending[:]:
        if prompt.search(buffer):
            os.write(descriptor, answer)
            pending.remove((prompt, answer))
            buffer = ""
    return data


def capture_timeout(signum, frame):
    os.spawnv(os.P_WAIT, "/bin/ps", ["ps", "-eo", "pid,ppid,user,tty,stat,args"])
    sys.exit(124)


signal.signal(signal.SIGALRM, capture_timeout)
signal.alarm(180)
status = os.waitstatus_to_exitcode(pty.spawn(sys.argv[2:], master_read=answer_shown_prompts))
signal.alarm(0)
print(f"TTY_CONSENT answered={expected - len(pending)} expected={expected}")
sys.exit(status or (78 if pending else 0))
