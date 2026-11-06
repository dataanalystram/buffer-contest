#!/bin/sh
# Licence-safe test clips for the One take media proof (Wikimedia Commons; sources + licences in SOURCES.md).
set -eu
cd "$(dirname "$0")"
mkdir -p src
UA="HuhQueueMediaProof/1.0 (somarajuramprasadie@gmail.com)"
B=https://upload.wikimedia.org/wikipedia/commons/transcoded
get() { [ -s "src/$1" ] || curl -fsSL -A "$UA" ${3:+-r 0-$3} -o "src/$1" "$2"; }
get kende.webm "$B/6/6d/Internet_Hall_of_Fame_2014_Michael_Kende_interview.webm/Internet_Hall_of_Fame_2014_Michael_Kende_interview.webm.720p.vp9.webm"
get cloke.webm "$B/9/99/Interview_on_extreme_weather_with_physical_geographer_Hannah_Cloke_%E2%80%93_The_Royal_Society.webm/Interview_on_extreme_weather_with_physical_geographer_Hannah_Cloke_%E2%80%93_The_Royal_Society.webm.720p.vp9.webm" 50000000
get redirects.webm "https://upload.wikimedia.org/wikipedia/commons/2/23/Wikipedia_Tutorial_Part_8_Redirects.webm"
get css.webm "$B/7/7b/The_CSS-Podcast_%E2%80%93_090_%E2%80%93_Scroll-driven_animations.webm/The_CSS-Podcast_%E2%80%93_090_%E2%80%93_Scroll-driven_animations.webm.720p.vp9.webm" 60000000
get caruso.webm "$B/d/d1/Interview_with_a_Teacher_-_Glen_Caruso.webm/Interview_with_a_Teacher_-_Glen_Caruso.webm.720p.vp9.webm" 60000000
F=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf
X="-c:v libx264 -preset veryfast -crf 18 -c:a aac -b:a 160k"
ffmpeg -nostdin -loglevel error -y -t 120 -i src/kende.webm $X v1_talkinghead_kende.mp4
ffmpeg -nostdin -loglevel error -y -t 150 -i src/cloke.webm $X v2_interview_cloke.mp4
ffmpeg -nostdin -loglevel error -y -t 153 -i src/redirects.webm $X v3_screen_redirects.mp4
# synthetic re-post watermark (TikTok wordmark + @handle, top-left for 60 s then bottom-right, like TikTok's moving mark)
ffmpeg -nostdin -loglevel error -y -ss 60 -t 120 -i src/css.webm -vf "drawtext=fontfile=$F:text='TikTok':fontsize=44:fontcolor=white@0.85:borderw=3:bordercolor=black@0.6:x='if(lt(t,60),40,w-tw-40)':y='if(lt(t,60),40,h-130)',drawtext=fontfile=$F:text='@scrollclips':fontsize=34:fontcolor=white@0.85:borderw=3:bordercolor=black@0.6:x='if(lt(t,60),40,w-tw-40)':y='if(lt(t,60),95,h-75)'" $X v4_watermark_css.mp4
ffmpeg -nostdin -loglevel error -y -t 150 -i src/caruso.webm $X v5_pauses_caruso.mp4
sha256sum v*.mp4 > clips.sha256
cat clips.sha256
