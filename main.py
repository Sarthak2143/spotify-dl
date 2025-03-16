import yt_dlp
import os
import multiprocessing
from concurrent.futures import ThreadPoolExecutor
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import json
import time
import requests
from tqdm import tqdm
import argparse
import signal
from mutagen.id3 import ID3, APIC, TIT2, TPE1, TALB, TDRC, TCON, TRCK
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover
from mutagen.flac import FLAC, Picture
from io import BytesIO

# Constants for configuration
SPOTIFY_SCOPE = "user-library-read"  # Scope for Spotify API access
YOUTUBE_SEARCH_LIMIT = 1  # Limit for YouTube search results
SPOTIFY_TRACK_LIMIT = 50  # Limit for Spotify track retrieval
YOUTUBE_RETRIES = 3  # Number of retries for YouTube search
DOWNLOAD_RETRIES = 3  # Number of retries for downloading audio
DOWNLOAD_BACKOFF = 2  # Backoff time for retries in seconds

# Global variable to track if we're exiting
exiting = False

def signal_handler():
    """
    Signal handler for graceful shutdown on interrupt signal.
    Sets the global 'exiting' flag to True.
    """
    global exiting
    exiting = True
    print("\nReceived interrupt signal. Finishing current downloads and exiting...")

# Register the signal handler for SIGINT (Ctrl+C)
signal.signal(signal.SIGINT, signal_handler)

# Loading Spotify credentials from config.json
with open("config.json") as file:
    data = json.load(file)
    client_id = data['CLIENT_ID']
    client_secret = data['CLIENT_SECRET']
    redirect_uri = data['REDIRECT_URI']

# Initialize Spotify client with OAuth credentials
sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
    client_id=client_id, client_secret=client_secret, 
    redirect_uri=redirect_uri, scope=SPOTIFY_SCOPE))

def get_youtube_url(song_name, artist_name, retries=YOUTUBE_RETRIES):
    for attempt in range(retries):
        try:
            time.sleep(attempt)  # Exponential backoff
            # Use yt_dlp to search YouTube
            with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
                search_query = f"ytsearch1:{song_name} {artist_name}"
                info = ydl.extract_info(search_query, download=False)
                if 'entries' in info and len(info['entries']) > 0:
                    return info['entries'][0]['webpage_url']
                else:
                    print(f"No YouTube results for: \"{song_name} {artist_name}\"")
                    return None
        except Exception as e:
            if attempt == retries - 1:
                print(f"Error searching for \"{song_name} {artist_name}\": {str(e)}")
                return None

def get_songs_url(url, limit=None):
    if "?" in url:
        # removes tracking, often spotify adds a share id in the url.
        url = url.split("?")[0]
    if "album" in url:
        return download_album(url)
    elif "playlist" in url:
        return download_playlist(url, limit)
    elif "spotify.com/user" in url:
        return download_user_library(limit)
    else:
        raise ValueError("Unknown URL format. Please use a Spotify album, playlist, or user library URL.")

def download_playlist(url, limit=None):
    return download_spotify_tracks(sp.playlist, sp.playlist_tracks, url, limit)

def download_album(url):
    return download_spotify_tracks(sp.album, lambda id, **kwargs: sp.album(id)['tracks'], url)

def download_user_library(limit=None):
    tracks = []
    offset = 0
    while True:
        results = sp.current_user_saved_tracks(limit=SPOTIFY_TRACK_LIMIT, offset=offset)
        tracks.extend(results['items'])
        if len(results['items']) < SPOTIFY_TRACK_LIMIT or (limit and len(tracks) >= limit):
            break
        offset += SPOTIFY_TRACK_LIMIT
    
    if limit:
        tracks = tracks[:limit]
    
    return process_tracks(tracks, "My Liked Songs", len(tracks), status="playlist")

def download_spotify_tracks(get_func, get_tracks_func, url, limit=None):
    """
    get_func is a variable function to get the album or playlist
    get_tracks_func is a var function to get the tracks of the album or playlist
    """
    id = url.split("/")[-1] # for ex: https://open.spotify.com/playlist/6G1yylbkuV3dxeOYhdeguk here ID: 6G1yylbkuV3dxeOYhdeguk
    item = get_func(id)
    total_tracks = item['tracks']['total']
    tracks = []
    
    for offset in range(0, total_tracks, 100):
        results = get_tracks_func(id, offset=offset, limit=100)
        tracks.extend(results['items'])
        if limit and len(tracks) >= limit:
            tracks = tracks[:limit]
            break
    if "album" in url:
        return process_tracks(tracks, item['name'], total_tracks, status="album")
    elif "playlist" in url:
        return process_tracks(tracks, item['name'], total_tracks, status="playlist")

# Structure to store track metadata
class TrackMetadata:
    def __init__(self, title, artist, album, year, track_number, genre, cover_url):
        self.title = title
        self.artist = artist
        self.album = album
        self.year = year
        self.track_number = track_number
        self.genre = genre
        self.cover_url = cover_url

def get_track_metadata(track, status):
    """Extract metadata from a Spotify track"""
    try:
        if status == "album":
            title = track["name"]
            artist = track['artists'][0]['name']
            album = track['album']['name'] if 'album' in track else ""
            year = track['album']['release_date'].split('-')[0] if 'album' in track and 'release_date' in track['album'] else ""
            track_number = str(track['track_number']) if 'track_number' in track else ""
            # Get genres from artist
            artist_info = sp.artist(track['artists'][0]['id'])
            genres = ", ".join(artist_info['genres']) if 'genres' in artist_info and artist_info['genres'] else ""
            # Get cover art URL
            cover_url = track['album']['images'][0]['url'] if 'album' in track and 'images' in track['album'] and track['album']['images'] else ""
        else:  # playlist
            track_obj = track['track']
            title = track_obj["name"]
            artist = track_obj['artists'][0]['name']
            album = track_obj['album']['name'] if 'album' in track_obj else ""
            year = track_obj['album']['release_date'].split('-')[0] if 'album' in track_obj and 'release_date' in track_obj['album'] else ""
            track_number = str(track_obj['track_number']) if 'track_number' in track_obj else ""
            # Get genres from artist
            artist_info = sp.artist(track_obj['artists'][0]['id'])
            genres = ", ".join(artist_info['genres']) if 'genres' in artist_info and artist_info['genres'] else ""
            # Get cover art URL
            cover_url = track_obj['album']['images'][0]['url'] if 'album' in track_obj and 'images' in track_obj['album'] and track_obj['album']['images'] else ""
        
        return TrackMetadata(title, artist, album, year, track_number, genres, cover_url)
    except Exception as e:
        print(f"Error extracting metadata: {str(e)}")
        return None

def download_cover_art(cover_url):
    """Download cover art from URL"""
    try:
        if not cover_url:
            return None
        response = requests.get(cover_url)
        if response.status_code == 200:
            return response.content
        return None
    except Exception as e:
        print(f"Error downloading cover art: {str(e)}")
        return None

def apply_metadata_to_file(filepath, metadata):
    """Apply metadata and cover art to audio file"""
    if not os.path.exists(filepath) or not metadata:
        return False
    
    try:
        file_ext = os.path.splitext(filepath)[1].lower()
        cover_data = download_cover_art(metadata.cover_url)
        
        if file_ext == '.mp3':
            audio = MP3(filepath, ID3=ID3)
            # Add ID3 tag if it doesn't exist
            try:
                audio.add_tags()
            except:
                pass
            
            # Set metadata
            audio.tags.add(TIT2(encoding=3, text=metadata.title))
            audio.tags.add(TPE1(encoding=3, text=metadata.artist))
            audio.tags.add(TALB(encoding=3, text=metadata.album))
            if metadata.year:
                audio.tags.add(TDRC(encoding=3, text=metadata.year))
            if metadata.track_number:
                audio.tags.add(TRCK(encoding=3, text=metadata.track_number))
            if metadata.genre:
                audio.tags.add(TCON(encoding=3, text=metadata.genre))
            
            # Add cover art
            if cover_data:
                audio.tags.add(APIC(
                    encoding=3,
                    mime='image/jpeg',
                    type=3,  # Cover (front)
                    desc='Cover',
                    data=cover_data
                ))
            
            audio.save()
            
        elif file_ext == '.m4a':
            audio = MP4(filepath)
            
            # Set metadata
            audio['\xa9nam'] = metadata.title
            audio['\xa9ART'] = metadata.artist
            audio['\xa9alb'] = metadata.album
            if metadata.year:
                audio['\xa9day'] = metadata.year
            if metadata.track_number:
                audio['trkn'] = [(int(metadata.track_number), 0)]
            if metadata.genre:
                audio['\xa9gen'] = metadata.genre
                
            # Add cover art
            if cover_data:
                audio['covr'] = [MP4Cover(cover_data, imageformat=MP4Cover.FORMAT_JPEG)]
                
            audio.save()
            
        elif file_ext == '.flac':
            audio = FLAC(filepath)
            
            # Set metadata
            audio['TITLE'] = metadata.title
            audio['ARTIST'] = metadata.artist
            audio['ALBUM'] = metadata.album
            if metadata.year:
                audio['DATE'] = metadata.year
            if metadata.track_number:
                audio['TRACKNUMBER'] = metadata.track_number
            if metadata.genre:
                audio['GENRE'] = metadata.genre
                
            # Add cover art
            if cover_data:
                picture = Picture()
                picture.type = 3  # Cover (front)
                picture.mime = "image/jpeg"
                picture.desc = "Cover"
                picture.data = cover_data
                
                audio.add_picture(picture)
                
            audio.save()
            
        return True
    except Exception as e:
        print(f"Error applying metadata to {filepath}: {str(e)}")
        return False

def process_tracks(tracks, name, total_tracks, status):
    print(f"Processing {len(tracks)} out of {total_tracks} tracks")
    url_list = []
    metadata_list = []
    not_found = []
    
    # Extract metadata for all tracks
    metadata_map = {}
    for track in tracks:
        metadata = get_track_metadata(track, status)
        if status == "album":
            track_title = track["name"]
            track_artist = track['artists'][0]['name']
        else:  # playlist
            track_title = track['track']["name"]
            track_artist = track['track']['artists'][0]['name']
            
        key = f"{track_title} - {track_artist}"
        metadata_map[key] = metadata
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        if status == "album":
            futures = [executor.submit(get_youtube_url, song["name"], song['artists'][0]['name']) for song in tracks]
        elif status == "playlist":
            futures = [executor.submit(get_youtube_url, song['track']["name"], song['track']['artists'][0]['name']) for song in tracks]
        
        for i, future in enumerate(futures):
            result = future.result()
            if result:
                if status == "album":
                    track_title = tracks[i]["name"]
                    track_artist = tracks[i]['artists'][0]['name']
                else:  # playlist
                    track_title = tracks[i]['track']["name"]
                    track_artist = tracks[i]['track']['artists'][0]['name']
                    
                key = f"{track_title} - {track_artist}"
                metadata = metadata_map.get(key)
                
                url_list.append(result)
                metadata_list.append(metadata)
            else:
                not_found.append(future)
    
    print(f"Found YouTube URLs for {len(url_list)} out of {len(tracks)} tracks")
    if not_found:
        print(f"Could not find YouTube URLs for {len(not_found)} tracks")
    
    return url_list, metadata_list, name

def download_youtube_audio(args):
    url, output_dir, audio_format, audio_quality, metadata = args
    global exiting
    if exiting:
        return False

    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': audio_format,
            'preferredquality': audio_quality,
        }],
        'outtmpl': os.path.join(output_dir, '%(title)s.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
    }

    for attempt in range(DOWNLOAD_RETRIES):
        if exiting:
            return False
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info_dict = ydl.extract_info(url, download=True)
                downloaded_file = ydl.prepare_filename(info_dict)
                # Get actual downloaded filename with extension
                file_path = os.path.splitext(downloaded_file)[0] + "." + audio_format
                
                # Apply metadata if available
                if metadata and os.path.exists(file_path):
                    apply_metadata_to_file(file_path, metadata)
                
            return True
        except Exception as e:
            if attempt == DOWNLOAD_RETRIES - 1:
                print(f"Error downloading {url}: {str(e)}")
                return False
            time.sleep(attempt * DOWNLOAD_BACKOFF)

def download_multiple(urls, metadata_list, output_dir, num_processes=5, audio_format='mp3', audio_quality='192'):
    global exiting
    os.makedirs(output_dir, exist_ok=True)
    
    with multiprocessing.Pool(processes=num_processes) as pool:
        args_list = [
            (url, output_dir, audio_format, audio_quality, metadata) 
            for url, metadata in zip(urls, metadata_list)
        ]
        results = []
        pbar = tqdm(total=len(urls), desc="Downloading")
        for result in pool.imap(download_youtube_audio, args_list):
            results.append(result)
            pbar.update(1)
            if exiting:
                pool.terminate()
                break
        pbar.close()
    
    success_count = sum(results)
    print(f"\nSuccessfully downloaded {success_count} out of {len(urls)} songs.")
    if exiting:
        print("Download process was interrupted. Some songs may not have been downloaded.")

if __name__ == "__main__":
    # adding arguments for better cli usablity
    parser = argparse.ArgumentParser(description="Download Spotify playlist/album as MP3")
    parser.add_argument("url", help="Spotify playlist/album URL or 'liked' for user's liked songs")
    parser.add_argument("-l", "--limit", type=int, help="Limit number of songs to download")
    parser.add_argument("-f", "--format", default="mp3", choices=["mp3", "m4a", "wav"], help="Audio format")
    parser.add_argument("-q", "--quality", default="192", choices=["128", "192", "256", "320"], help="Audio quality (bitrate)")
    args = parser.parse_args()

    try:
        if args.url.lower() == 'liked': # liked songs
            urls, metadata_list, output_dir = download_user_library(args.limit)
        else:
            urls, metadata_list, output_dir = get_songs_url(args.url, args.limit)

        num_processes = min(multiprocessing.cpu_count(), 5)
        
        print(f"Attempting to download {len(urls)} songs to '{output_dir}'...")
        download_multiple(urls, metadata_list, output_dir, num_processes, args.format, args.quality)
        
        if not exiting:
            print("All downloads completed.")
            print(f"Successfully downloaded {len([f for f in os.listdir(output_dir) if f.endswith('.' + args.format)])} songs.")

    except KeyboardInterrupt:
        print("\nScript interrupted by user. Exiting...")
    finally:
        if exiting:
            print("Download process finished. Some songs may not have been downloaded due to interruption.")
