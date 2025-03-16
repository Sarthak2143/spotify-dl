import os
import time
import threading
from main import download_youtube_audio
import multiprocessing

# This is a global variable accessible to the main process only
# It won't be shared with worker processes
_progress_callbacks = {}

def download_with_progress(args):
    """
    Simple wrapper for download_youtube_audio
    Args:
        args: The download arguments tuple (url, output_dir, audio_format, audio_quality, metadata)
    """
    return download_youtube_audio(args)

def download_with_tracking(task_id, urls, metadata_list, output_dir, num_processes, audio_format, audio_quality, callback):
    """
    Download multiple songs with progress tracking
    Args:
        task_id: ID of the task for callback registration
        urls: List of YouTube URLs to download
        metadata_list: List of metadata objects corresponding to URLs
        output_dir: Directory to save downloaded files
        num_processes: Number of processes to use
        audio_format: Audio format (mp3, m4a, wav)
        audio_quality: Audio quality (128, 192, 256, 320)
        callback: Callback function to call after each download
    Returns:
        Number of successfully downloaded songs
    """
    os.makedirs(output_dir, exist_ok=True)
    
    # Prepare arguments list
    args_list = [
        (url, output_dir, audio_format, audio_quality, metadata) 
        for url, metadata in zip(urls, metadata_list)
    ]
    
    # Create a proxy for the results that updates progress
    results = []
    
    # Use a pool to download files
    with multiprocessing.Pool(processes=num_processes) as pool:
        # Start a simple thread to monitor downloads and update progress
        completed = 0
        total = len(args_list)
        
        def progress_monitor():
            nonlocal completed
            while completed < total:
                # Sleep to reduce CPU usage
                time.sleep(0.5)
                
                # Check if we have new results
                current = len(results)
                if current > completed:
                    # Update progress for each newly completed download
                    for _ in range(current - completed):
                        if callback:
                            callback()
                    completed = current
        
        # Start the monitor thread
        monitor = threading.Thread(target=progress_monitor)
        monitor.daemon = True
        monitor.start()
        
        # Start the downloads
        for result in pool.imap_unordered(download_with_progress, args_list):
            results.append(result)
        
        # Wait for the monitor to catch up
        monitor.join(timeout=1.0)
    
    return sum(results)
