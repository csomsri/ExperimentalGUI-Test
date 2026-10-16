# -*- coding: utf-8 -*-
"""
Created on Wed Jul 23 19:11:33 2025

@author: clasa
"""

# zmq_field_controller_sender.py
import zmq
import struct
import time

NUM_CHANNELS = 14
ZMQ_PORT = 5566  # LabVIEW should PULL from this port
EPOCH_OFFSET = 2082844800

def controller_sender_main(control_queue):
    context = zmq.Context()
    socket = context.socket(zmq.PUSH)
    socket.bind(f"tcp://*:{ZMQ_PORT}")
    print(f"🛰️ Field Controller ZMQ client started at tcp://*:{ZMQ_PORT}")

    while True:
        try:
            # Wait for a (targets, on_off, enable) tuple from GUI
            targets, on_off, enable = control_queue.get()

            # Build 28-bit bitmask
            bitmask = sum([(int(on_off[i]) << i) | (int(enable[i]) << (14 + i)) for i in range(NUM_CHANNELS)])

            # Build data packet: timestamp + 14 targets + bitmask
            timestamp = time.time() + EPOCH_OFFSET
            packet = struct.pack("<16d", timestamp, *targets, float(bitmask))

            # Send to LabVIEW (or simulator)
            socket.send(packet)

            print(f"✅ Sent control packet → {[round(v, 1) for v in targets]}")
            print(f"🟢 Bitmask: {bitmask:028b} | Targets[0]: {targets[0]}")

        except Exception as e:
            print("❌ Controller send failed:", e)
