# Spotify Downloader

A Python application that downloads songs from Spotify playlists, albums, or your liked songs as MP3 files (or other audio formats). The tool searches for each track on YouTube and downloads the audio in your preferred format and quality. It features both a command-line interface and a web interface for easier use.

## Features

- Download entire Spotify playlists, albums, or your liked songs
- Concurrent downloads for faster processing
- Configurable audio format (MP3, M4A, WAV)
- Adjustable audio quality (128kbps to 320kbps)
- Progress bar with download status
- Graceful handling of interruptions
- Automatic retry mechanism for failed downloads
- Multithreaded YouTube URL fetching
- Web interface for easier use
- Batch mode for downloading multiple playlists/albums at once
- Real-time progress tracking
- Custom download location support

![screenshot](screenshot.png)

## Prerequisites

- Python 3.9+
- FFmpeg installed on your system
- Required Python packages (install via pip):

  ```bash
  pip install -r requirements.txt
  ```

## Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/sarthak2143/spotify-dl.git
   cd spotify-dl
   ```

2. Create a Spotify Developer account and get your credentials:
   - Go to [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
   - Create a new application
   - Get your Client ID and Client Secret
   - Add `http://localhost:5000/callback` to your Redirect URIs in the app settings

3. Create a `config.json` file in the project directory:

   ```json
   {
       "CLIENT_ID": "your_client_id_here",
       "CLIENT_SECRET": "your_client_secret_here",
       "REDIRECT_URI": "http://localhost:5000/callback"
   }
   ```

## Usage

The application can be used in two ways: through the command-line interface or web interface.

### Command Line Interface

#### Basic Usage

```bash
# Download a playlist
python main.py "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"

# Download an album
python main.py "https://open.spotify.com/album/1DFixLWuPkv3KT3TnV35m3"

# Download your liked songs
python main.py "liked"
```

#### Advanced Options

```bash
# Limit the number of songs to download (e.g., first 10 songs)
python main.py "playlist_url" -l 10

# Specify audio format (mp3, m4a, or wav)
python main.py "playlist_url" -f m4a

# Set audio quality (128, 192, 256, or 320 kbps)
python main.py "playlist_url" -q 320

# Combine multiple options
python main.py "playlist_url" -l 5 -f mp3 -q 320
```

#### Command Line Arguments

- `url`: Spotify playlist/album URL or 'liked' for your liked songs
- `-l, --limit`: Limit the number of songs to download
- `-f, --format`: Audio format (mp3, m4a, wav)
- `-q, --quality`: Audio quality in kbps (128, 192, 256, 320)

### Web Interface

The web interface provides an easier way to download Spotify content with a user-friendly UI and real-time progress tracking.

#### Starting the Web Server

```bash
python app.py
```

This will start a Flask web server on port 5001. Open your browser and navigate to:

```
http://localhost:5001
```

#### Using the Web Interface

1. Enter a Spotify URL in the input field (playlist, album, or type "liked" for your liked songs)
2. Choose your preferred audio format and quality
3. Optionally specify a custom download location and limit
4. Toggle "Batch Mode" to download multiple playlists/albums at once (enter one URL per line)
5. Click "Download" and monitor the progress in real-time

## Acknowledgments

- [yt-dlp](https://github.com/yt-dlp/yt-dlp) for YouTube downloading functionality
- [spotipy](https://spotipy.readthedocs.io/) for Spotify API integration
- [youtube-search-python](https://github.com/alexmercerind/youtube-search-python) for YouTube search functionality
- [Flask](https://flask.palletsprojects.com/) for the web interface
