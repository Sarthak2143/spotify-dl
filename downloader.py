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
    Simple wrapper for download_youtube_audio that can be used with multiprocessing.
    
    Args:
        args: A tuple containing (url, output_dir, audio_format, audio_quality, metadata)
              These arguments are passed to download_youtube_audio
              
    Returns:
        Boolean indicating whether the download was successful
    """
    return download_youtube_audio(args)

def download_with_tracking(task_id, urls, metadata_list, output_dir, num_processes, audio_format, audio_quality, callback):
    """
    Download multiple songs with progress tracking.
    Manages a pool of worker processes and updates progress.
    
    Args:
        task_id: ID of the task for identification
        urls: List of YouTube URLs to download
        metadata_list: List of metadata objects corresponding to URLs
        output_dir: Directory to save downloaded files
        num_processes: Number of simultaneous download processes to use
        audio_format: Audio format (mp3, m4a, wav)
        audio_quality: Audio quality (128, 192, 256, 320)
        callback: Function to call after each download completes
        
    Returns:
        Number of successfully downloaded songs
    """
    # Create output directory if it doesn't exist
    os.makedirs(output_dir, exist_ok=True)
    
    # Prepare arguments list for each download
    args_list = [
        (url, output_dir, audio_format, audio_quality, metadata) 
        for url, metadata in zip(urls, metadata_list)
    ]
    
    # Store results as they complete
    results = []
    
    # Start a process pool for parallel downloads
    with multiprocessing.Pool(processes=num_processes) as pool:
        # Track how many downloads have completed
        completed = 0
        total = len(args_list)
        
        # This function runs in a separate thread to monitor progress
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
        monitor.daemon = True  # Thread will exit when main thread exits
        monitor.start()
        
        # Start the downloads using imap_unordered for better performance
        # This returns results as they complete rather than in order
        for result in pool.imap_unordered(download_with_progress, args_list):
            results.append(result)
        
        # Wait for the monitor to catch up with any final results
        monitor.join(timeout=1.0)
    
    # Return the number of successful downloads
    return sum(results)
