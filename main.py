import sys
import multiprocessing
from zmq_rep_server import zmq_server_main
from main_control_gui import run_gui
from sqlite_logger import sqlite_logger

def main():
    print("🚀 Starting Cyclotron Control System")

    # Queues for communication between processes
    data_queue = multiprocessing.Queue()
    control_queue = multiprocessing.Queue()
    log_queue = multiprocessing.Queue()  # 🧠 for SQLite logging

    # Shared data objects via Manager
    manager = multiprocessing.Manager()
    target_values = manager.list([0.0] * 14)
    plot_config = manager.dict({
        "channels": [False] * 14,
        "duration": 2,
        "rate": 240
    })

    # Start ZMQ Server Process (communicates with LabVIEW)
    zmq_process = multiprocessing.Process(
        target=zmq_server_main,
        args=(data_queue, target_values, control_queue, log_queue),
        daemon=True
    )
    zmq_process.start()

    # Start SQLite Logger Process
    logger_process = multiprocessing.Process(
        target=sqlite_logger,
        args=(log_queue,),
        daemon=True
    )
    logger_process.start()

    # Launch GUI (Controller + Monitoring)
    run_gui(data_queue, target_values, control_queue, plot_config)

    # Optional: Join processes if not daemonized
    zmq_process.join()
    logger_process.join()

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
