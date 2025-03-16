from flask import Flask, request, render_template, redirect, url_for, flash, session, jsonify
import os
import multiprocessing
import threading
import uuid
import time
import json
from main import get_songs_url, download_user_library
from downloader import download_with_tracking

app = Flask(__name__)
app.secret_key = "spotify-dl-secret-key"  # Required for flash and session

# Store for download tasks and their progress
download_tasks = {}

class DownloadTask:
    def __init__(self, task_id, total_songs, original_url=""):
        self.id = task_id
        self.total = total_songs
        self.completed = 0
        self.status = "preparing"  # preparing, processing, downloading, completed, error
        self.error = None
        self.output_dir = None
        self.completion_time = None
        self.original_url = original_url
        self.is_batch = False
        self.sub_tasks = {}  # For batch downloads to track individual URL progress

def progress_callback(task_id):
    """Callback function to update download progress"""
    task = download_tasks.get(task_id)
    if task:
        task.completed += 1

def background_download(task_id, urls, metadata_list, output_dir, num_processes, audio_format, audio_quality):
    task = download_tasks.get(task_id)
    if not task:
        return
    
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
        
        # Make sure the completion count is accurate
        task.completed = len(urls)
        task.status = "completed"
        task.completion_time = time.time()
    except Exception as e:
        task.error = str(e)
        task.status = "error"
        task.completion_time = time.time()

@app.route('/')
def index():
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
        
    return render_template('index.html', error=error, success=success, task_id=task_id)

@app.route('/download', methods=['POST'])
def download():
    url = request.form['url']
    limit = request.form.get('limit', type=int)
    audio_format = request.form.get('format', 'mp3')
    audio_quality = request.form.get('quality', '192')
    custom_output_dir = request.form.get('output_dir', '')
    batch_mode = request.form.get('batch_mode') == 'true'

    try:
        if not url:
            session['error'] = "Please enter a Spotify URL or 'liked'"
            return redirect(url_for('index'))
        
        # Handle batch mode
        if batch_mode:
            # Split URLs by newline and filter out empty lines
            urls_list = [u.strip() for u in url.splitlines() if u.strip()]
            
            if not urls_list:
                session['error'] = "No valid URLs found"
                return redirect(url_for('index'))
                
            # Create a batch task
            return handle_batch_download(urls_list, limit, audio_format, audio_quality, custom_output_dir)
        else:
            # Handle single URL (existing functionality)
            if url.lower() == 'liked':
                urls, metadata_list, output_dir = download_user_library(limit)
            else:
                urls, metadata_list, output_dir = get_songs_url(url, limit)
                
            # Override output directory if specified
            if custom_output_dir:
                output_dir = os.path.abspath(custom_output_dir)
                # Create the directory if it doesn't exist
                os.makedirs(output_dir, exist_ok=True)

            if not urls or len(urls) == 0:
                session['error'] = "No songs found to download"
                return redirect(url_for('index'))

            # Create a unique task ID and store task info
            task_id = str(uuid.uuid4())
            task = DownloadTask(task_id, len(urls), url)
            download_tasks[task_id] = task
            session['task_id'] = task_id
            
            # Start download in background thread
            num_processes = min(multiprocessing.cpu_count(), 5)
            thread = threading.Thread(
                target=background_download,
                args=(task_id, urls, metadata_list, output_dir, num_processes, audio_format, audio_quality)
            )
            thread.daemon = True
            thread.start()
            
            # Redirect to the main page which will now show progress
            return redirect(url_for('index'))
            
    except ValueError as e:
        session['error'] = str(e)
        return redirect(url_for('index'))
    except Exception as e:
        session['error'] = f"An unexpected error occurred: {str(e)}"
        return redirect(url_for('index'))

def handle_batch_download(urls_list, limit, audio_format, audio_quality, custom_output_dir):
    """Process a batch of URLs for download"""
    # Create a unique batch ID
    batch_id = str(uuid.uuid4())
    
    # Create a base output directory for the entire batch
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
    """Process each URL in the batch and download its content"""
    master_task = download_tasks.get(batch_id)
    
    if not master_task:
        return
        
    master_task.status = "processing"
    
    total_songs = 0
    all_urls = []
    all_metadata = []
    url_folders = {}  # Map URLs to their individual folders
    
    try:
        # Process each URL and gather info
        for url in urls_list:
            try:
                # Create a subfolder for this URL
                url_name = url.split('/')[-1] if '/' in url else url
                if url.lower() == 'liked':
                    url_name = 'liked_songs'
                url_folder = os.path.join(base_output_dir, url_name)
                os.makedirs(url_folder, exist_ok=True)
                url_folders[url] = url_folder
                
                # Get song information
                if url.lower() == 'liked':
                    song_urls, metadata_list, _ = download_user_library(limit)
                else:
                    song_urls, metadata_list, _ = get_songs_url(url, limit)
                
                if song_urls and len(song_urls) > 0:
                    all_urls.extend(song_urls)
                    all_metadata.extend(metadata_list)
                    total_songs += len(song_urls)
                
            except Exception as e:
                # Log error but continue with other URLs
                print(f"Error processing URL {url}: {str(e)}")
        
        # Update master task with total song count
        master_task.total = total_songs
        
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
        num_processes = min(multiprocessing.cpu_count(), 5)
        download_with_tracking(
            batch_id, all_urls, all_metadata, base_output_dir, 
            num_processes, audio_format, audio_quality, batch_progress_update
        )
        
        # Mark as completed
        master_task.completed = total_songs
        master_task.status = "completed"
        master_task.completion_time = time.time()
        
    except Exception as e:
        master_task.error = str(e)
        master_task.status = "error"
        master_task.completion_time = time.time()

@app.route('/check_progress/<task_id>')
def check_progress(task_id):
    """Endpoint to check the progress of a download task"""
    task = download_tasks.get(task_id)
    if not task:
        return jsonify({
            'status': 'not_found'
        }), 404
        
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
    """Endpoint to clear the current task and start a new download"""
    session.pop('task_id', None)
    return jsonify({'success': True})

# Clean up old tasks
def cleanup_old_tasks():
    current_time = time.time()
    for task_id in list(download_tasks.keys()):
        task = download_tasks[task_id]
        if task.status in ["completed", "error"] and task.completion_time and current_time - task.completion_time > 3600:  # 1 hour
            del download_tasks[task_id]

def periodic_cleanup():
    """Periodically clean up old tasks"""
    while True:
        time.sleep(300)  # Run every 5 minutes
        cleanup_old_tasks()

if __name__ == "__main__":
    # Start a thread for cleanup
    cleanup_thread = threading.Thread(target=periodic_cleanup)
    cleanup_thread.daemon = True
    cleanup_thread.start()
    
    app.run(debug=True, port=5001)

