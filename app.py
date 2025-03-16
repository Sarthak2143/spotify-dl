from flask import Flask, request, render_template, redirect, url_for, flash, session, jsonify
import os
import multiprocessing
import threading
import uuid
import time
import json
from main import get_songs_url, download_user_library
from downloader import download_with_tracking

# Initialize Flask app
app = Flask(__name__)
app.secret_key = "spotify-dl-secret-key"  # Required for flash and session

# Global store for tracking download tasks and their progress across requests
download_tasks = {}

class DownloadTask:
    """
    Class to represent a download task and its progress.
    Each task has a unique ID and maintains its own state.
    """
    def __init__(self, task_id, total_songs, original_url=""):
        self.id = task_id                    # Unique task identifier
        self.total = total_songs             # Total number of songs to download
        self.completed = 0                   # Number of songs downloaded so far
        self.status = "preparing"            # Current status: preparing, processing, downloading, completed, error
        self.error = None                    # Error message if any
        self.output_dir = None               # Directory where songs are downloaded to
        self.completion_time = None          # When the task was completed or errored
        self.original_url = original_url     # URL that was requested for download
        self.is_batch = False                # Whether this is a batch download of multiple URLs
        self.sub_tasks = {}                  # For batch downloads to track individual URL progress

def progress_callback(task_id):
    """
    Callback function to update download progress for a task.
    This is called whenever a song is successfully downloaded.
    
    Args:
        task_id: The unique identifier for the task to update
    """
    task = download_tasks.get(task_id)
    if task:
        task.completed += 1

def background_download(task_id, urls, metadata_list, output_dir, num_processes, audio_format, audio_quality):
    """
    Background worker function that handles the actual download process.
    This runs in a separate thread to avoid blocking the main Flask thread.
    
    Args:
        task_id: The unique identifier for this download task
        urls: List of YouTube URLs to download
        metadata_list: List of metadata for each song
        output_dir: Directory to save the downloaded files to
        num_processes: Number of parallel download processes to use
        audio_format: Format to convert audio to (mp3, m4a, wav)
        audio_quality: Audio quality/bitrate (128, 192, 256, 320)
    """
    task = download_tasks.get(task_id)
    if not task:
        return
    
    # Update task status to indicate download is starting
    task.status = "downloading"
    task.output_dir = output_dir
    
    try:
        # Define a mutable counter object that can be updated by callbacks
        counter = [0]  # Using a list as a mutable container
        
        # Create a callback that updates the task's completed count
        def progress_update():
            counter[0] += 1
            task.completed = counter[0]
        
        # Use our helper function for downloading with progress tracking
        success_count = download_with_tracking(
            task_id, urls, metadata_list, output_dir, num_processes, 
            audio_format, audio_quality, progress_update
        )
        
        # Make sure the completion count is accurate and update task status
        task.completed = len(urls)
        task.status = "completed"
        task.completion_time = time.time()
    except Exception as e:
        # Handle any errors that occur during download
        task.error = str(e)
        task.status = "error"
        task.completion_time = time.time()

@app.route('/')
def index():
    """
    Main route that renders the homepage.
    Shows download form or progress based on session state.
    """
    # Get any messages from the session
    error = session.pop('error', None)
    success = session.pop('success', None)
    task_id = session.get('task_id')
    
    # Verify task actually exists and is still active
    if task_id and task_id in download_tasks:
        task = download_tasks[task_id]
        if task.status in ["completed", "error"]:
            # Task is finished, no need to keep tracking
            session.pop('task_id', None)
            task_id = None
    else:
        # Task doesn't exist, clear it
        session.pop('task_id', None)
        task_id = None
        
    # Render the index template with necessary data
    return render_template('index.html', error=error, success=success, task_id=task_id)

@app.route('/download', methods=['POST'])
def download():
    """
    Handle download form submissions.
    Processes the URL(s) and starts a download task.
    """
    # Get form data
    url = request.form['url']
    limit = request.form.get('limit', type=int)
    audio_format = request.form.get('format', 'mp3')
    audio_quality = request.form.get('quality', '192')
    custom_output_dir = request.form.get('output_dir', '')
    batch_mode = request.form.get('batch_mode') == 'true'

    try:
        # Validate input
        if not url:
            session['error'] = "Please enter a Spotify URL or 'liked'"
            return redirect(url_for('index'))
        
        # Handle batch mode (multiple URLs)
        if batch_mode:
            # Split URLs by newline and filter out empty lines
            urls_list = [u.strip() for u in url.splitlines() if u.strip()]
            
            if not urls_list:
                session['error'] = "No valid URLs found"
                return redirect(url_for('index'))
                
            # Create a batch task to handle multiple URLs
            return handle_batch_download(urls_list, limit, audio_format, audio_quality, custom_output_dir)
        else:
            # Handle single URL download
            if url.lower() == 'liked':
                # Special case for user's liked songs
                urls, metadata_list, output_dir = download_user_library(limit)
            else:
                # Normal case for playlists or albums
                urls, metadata_list, output_dir = get_songs_url(url, limit)
                
            # Override output directory if specified
            if custom_output_dir:
                output_dir = os.path.abspath(custom_output_dir)
                # Create the directory if it doesn't exist
                os.makedirs(output_dir, exist_ok=True)

            # Validate that we have songs to download
            if not urls or len(urls) == 0:
                session['error'] = "No songs found to download"
                return redirect(url_for('index'))

            # Create a unique task ID and store task info
            task_id = str(uuid.uuid4())
            task = DownloadTask(task_id, len(urls), url)
            download_tasks[task_id] = task
            session['task_id'] = task_id
            
            # Start download in background thread
            num_processes = min(multiprocessing.cpu_count(), 5)  # Limit to 5 processes max
            thread = threading.Thread(
                target=background_download,
                args=(task_id, urls, metadata_list, output_dir, num_processes, audio_format, audio_quality)
            )
            thread.daemon = True  # Thread will be terminated when main process exits
            thread.start()
            
            # Redirect to the main page which will now show progress
            return redirect(url_for('index'))
            
    except ValueError as e:
        # Handle expected errors with informative messages
        session['error'] = str(e)
        return redirect(url_for('index'))
    except Exception as e:
        # Handle unexpected errors
        session['error'] = f"An unexpected error occurred: {str(e)}"
        return redirect(url_for('index'))

def handle_batch_download(urls_list, limit, audio_format, audio_quality, custom_output_dir):
    """
    Process a batch of URLs for download.
    Creates a master task and processes each URL in a background thread.
    
    Args:
        urls_list: List of Spotify URLs to process
        limit: Maximum number of songs to download per URL (optional)
        audio_format: Format to convert audio to (mp3, m4a, wav)
        audio_quality: Audio quality/bitrate (128, 192, 256, 320)
        custom_output_dir: User-specified output directory (optional)
    """
    # Create a unique batch ID
    batch_id = str(uuid.uuid4())
    
    # Create a base output directory for the entire batch with timestamp
    batch_dir_name = f'batch_{time.strftime("%Y%m%d-%H%M%S")}'
    base_output_dir = custom_output_dir if custom_output_dir else os.path.join(os.getcwd(), 'downloads', batch_dir_name)
    os.makedirs(base_output_dir, exist_ok=True)
    
    # Create a master task to track overall progress
    master_task = DownloadTask(batch_id, len(urls_list))
    master_task.is_batch = True
    master_task.status = "processing"
    master_task.output_dir = base_output_dir
    download_tasks[batch_id] = master_task
    session['task_id'] = batch_id
    
    # Start the batch processing in a background thread
    thread = threading.Thread(
        target=process_batch,
        args=(batch_id, urls_list, limit, audio_format, audio_quality, base_output_dir)
    )
    thread.daemon = True
    thread.start()
    
    return redirect(url_for('index'))

def process_batch(batch_id, urls_list, limit, audio_format, audio_quality, base_output_dir):
    """
    Process each URL in a batch and download its content.
    This runs in a background thread.
    
    Args:
        batch_id: Unique ID for the batch task
        urls_list: List of URLs to process
        limit: Maximum number of songs per URL
        audio_format: Format to convert audio to (mp3, m4a, wav)
        audio_quality: Audio quality/bitrate (128, 192, 256, 320)
        base_output_dir: Directory to save all downloads
    """
    master_task = download_tasks.get(batch_id)
    
    if not master_task:
        return
        
    master_task.status = "processing"
    
    # Collect info about all songs to download
    total_songs = 0
    all_urls = []
    all_metadata = []
    url_folders = {}  # Map URLs to their individual folders
    
    try:
        # Process each URL and gather song info
        for url in urls_list:
            try:
                # Create a subfolder for this URL
                url_name = url.split('/')[-1] if '/' in url else url
                if url.lower() == 'liked':
                    url_name = 'liked_songs'
                url_folder = os.path.join(base_output_dir, url_name)
                os.makedirs(url_folder, exist_ok=True)
                url_folders[url] = url_folder
                
                # Get song information from Spotify
                if url.lower() == 'liked':
                    song_urls, metadata_list, _ = download_user_library(limit)
                else:
                    song_urls, metadata_list, _ = get_songs_url(url, limit)
                
                # Add found songs to our lists
                if song_urls and len(song_urls) > 0:
                    all_urls.extend(song_urls)
                    all_metadata.extend(metadata_list)
                    total_songs += len(song_urls)
                
            except Exception as e:
                # Log error but continue with other URLs
                print(f"Error processing URL {url}: {str(e)}")
        
        # Update master task with total song count
        master_task.total = total_songs
        
        # Handle case where no songs were found
        if total_songs == 0:
            master_task.status = "error"
            master_task.error = "No songs found to download in any of the URLs"
            master_task.completion_time = time.time()
            return
        
        # Now download all songs    
        master_task.status = "downloading"
        
        # Define a progress update callback for the batch
        def batch_progress_update():
            master_task.completed += 1
        
        # Use a single download operation for all songs
        num_processes = min(multiprocessing.cpu_count(), 5)  # Limit to 5 processes max
        download_with_tracking(
            batch_id, all_urls, all_metadata, base_output_dir, 
            num_processes, audio_format, audio_quality, batch_progress_update
        )
        
        # Mark as completed
        master_task.completed = total_songs
        master_task.status = "completed"
        master_task.completion_time = time.time()
        
    except Exception as e:
        # Handle any errors in the batch process
        master_task.error = str(e)
        master_task.status = "error"
        master_task.completion_time = time.time()

@app.route('/check_progress/<task_id>')
def check_progress(task_id):
    """
    API endpoint to check the progress of a download task.
    Used by the frontend to update the progress bar.
    
    Args:
        task_id: The unique identifier for the task
    """
    task = download_tasks.get(task_id)
    if not task:
        return jsonify({
            'status': 'not_found'
        }), 404
        
    # Return task status as JSON
    return jsonify({
        'status': task.status,
        'total': task.total,
        'completed': task.completed,
        'progress': int((task.completed / task.total) * 100) if task.total > 0 else 0,
        'error': task.error,
        'output_dir': task.output_dir,
        'is_batch': task.is_batch
    })

@app.route('/clear_task', methods=['POST'])
def clear_task():
    """
    API endpoint to clear the current task and start a new download.
    Called when the user clicks "Start New Download".
    """
    session.pop('task_id', None)
    return jsonify({'success': True})

def cleanup_old_tasks():
    """
    Remove old completed or errored tasks to prevent memory leaks.
    Tasks older than 1 hour are removed.
    """
    current_time = time.time()
    for task_id in list(download_tasks.keys()):
        task = download_tasks[task_id]
        # Remove tasks that are completed/errored and older than 1 hour
        if task.status in ["completed", "error"] and task.completion_time and current_time - task.completion_time > 3600:
            del download_tasks[task_id]

def periodic_cleanup():
    """
    Periodically run cleanup_old_tasks every 5 minutes.
    Runs in a separate thread.
    """
    while True:
        time.sleep(300)  # Run every 5 minutes
        cleanup_old_tasks()

if __name__ == "__main__":
    # Start a background thread for cleanup
    cleanup_thread = threading.Thread(target=periodic_cleanup)
    cleanup_thread.daemon = True
    cleanup_thread.start()
    
    # Start the Flask web server
    app.run(debug=True, port=5001)

