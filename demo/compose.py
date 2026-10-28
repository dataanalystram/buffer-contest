import json, os, subprocess, glob
R = "raw"; FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
c1 = glob.glob(R + "/c1/*.webm")[0]; c2 = glob.glob(R + "/c2/*.webm")[0]
m1 = json.load(open(R + "/c1_marks.json")); m2 = json.load(open(R + "/c2_marks.json"))
os.makedirs("cap", exist_ok=True); os.makedirs("seg", exist_ok=True)
def run(cmd): subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

C1 = [(m1["c1_landing"] - 0.5, m1["c1_buffer_auth"], "A creator's daily chore: one news link, a post for every channel"),
      (m1["c1_buffer_auth"], m1["c1_end"], "Connect with Buffer: OAuth 2.0 + PKCE. Tokens never reach the browser")]
C2 = [(3.5, m2["type_link"], "Your Buffer channels, read live from the Buffer API"),
      (m2["type_link"], m2["drafts_ready"] + 0.3, "Paste a news link"),
      (m2["drafts_ready"] + 0.3, m2["drafts_view"] + 0.5, "One AI draft per channel, counted with Buffer's character rules"),
      (m2["drafts_view"] + 0.5, m2["send_all"], "Edit anything. Each counter tracks that network's limit"),
      (m2["send_all"], m2["queue"], "Send all to Buffer: real createPost calls, saved as Buffer drafts"),
      (m2["queue"], m2["approvals"], "Read back from Buffer: the drafts are in your account"),
      (m2["approvals"], m2["agents"], "Agents post through MCP, but wait here until you approve"),
      (m2["agents"], m2["activity"], "Kill switch, daily caps, single-use confirm tokens"),
      (m2["activity"], m2["c2_end"], "Every action by you or your agents, in one activity log")]

def segment(src, caps, start, end, out, vertical=False):
    filt = []
    if vertical:
        filt.append("crop=1280:1080:320:0,scale=1080:911,pad=1080:1920:0:560:color=0xFAF9FC")
    else:
        filt.append("scale=1920:1080,setsar=1")
    for i, (a, b, text) in enumerate(caps):
        a, b = max(a, start), min(b, end)
        if b <= a: continue
        f = f"cap/{os.path.basename(out)}_{i}.txt"; open(f, "w").write(text)
        ta, tb = a - start, b - start
        if vertical:
            import textwrap
            for j, line in enumerate(textwrap.wrap(text, 34)):
                lf = f"cap/{os.path.basename(out)}_{i}_{j}.txt"; open(lf, "w").write(line)
                filt.append(f"drawtext=fontfile={FONT}:textfile={lf}:fontsize=42:fontcolor=0x16131F:x=(w-text_w)/2:y={1530 + j * 66}:"
                            f"box=1:boxcolor=0xC9ACFA:boxborderw=14:enable='between(t,{ta:.2f},{tb:.2f})'")
        else:
            filt.append(f"drawtext=fontfile={FONT}:textfile={f}:fontsize=40:fontcolor=white:x=(w-text_w)/2:y=h-120:"
                        f"box=1:boxcolor=0x16131F@0.88:boxborderw=24:enable='between(t,{ta:.2f},{tb:.2f})'")
    if vertical:
        for j, line in enumerate(["Paste a link. Post everywhere.", "Your AI agents need your OK."]):
            open(f"cap/vt{j}.txt", "w").write(line)
            filt.append(f"drawtext=fontfile={FONT}:textfile=cap/vt{j}.txt:fontsize=56:fontcolor=0x16131F:x=(w-text_w)/2:y={260 + j * 84}")
        open("cap/pill.txt", "w").write("Follow @huhbyte")
        filt.append(f"drawtext=fontfile={FONT}:textfile=cap/pill.txt:fontsize=40:fontcolor=0x16131F:x=(w-text_w)/2:y=1790:box=1:boxcolor=0xC9ACFA:boxborderw=20")
        open("cap/brand.txt", "w").write("HuhQueue — built on the Buffer API")
        filt.append(f"drawtext=fontfile={FONT}:textfile=cap/brand.txt:fontsize=34:fontcolor=0x7C4DDB:x=(w-text_w)/2:y=150")
    run(["ffmpeg", "-y", "-ss", f"{start:.2f}", "-to", f"{end:.2f}", "-i", src, "-vf", ",".join(filt) + ",fps=30,format=yuv420p",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-an", out])

def still(img, secs, out, size):
    run(["ffmpeg", "-y", "-loop", "1", "-t", str(secs), "-i", img, "-vf", f"scale={size},fps=30,format=yuv420p,fade=t=in:st=0:d=0.4",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", out])

def concat(parts, out, audio=None):
    open("seg/list.txt", "w").write("".join(f"file '{os.path.abspath(p)}'\n" for p in parts))
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "seg/list.txt", "-c", "copy", "seg/_v.mp4"])
    dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", "seg/_v.mp4"]))
    if audio:
        run(["ffmpeg", "-y", "-i", "seg/_v.mp4", "-i", audio, "-filter_complex", f"[1:a]volume=0.55,afade=t=out:st={dur-2.5:.2f}:d=2.5[a]",
             "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest", out])
    else:
        run(["ffmpeg", "-y", "-i", "seg/_v.mp4", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-map", "0:v", "-map", "1:a",
             "-c:v", "copy", "-c:a", "aac", "-shortest", out])
    return dur

# 16:9 contest demo (clean, no music)
segment(c1, C1, m1["c1_landing"] - 0.5, m1["c1_end"], "seg/h1.mp4")
segment(c2, C2, 3.5, m2["c2_end"], "seg/h2.mp4")
still("title_16x9.png", 3.5, "seg/ht.mp4", "1920:1080"); still("mcp_16x9.png", 7, "seg/hm.mp4", "1920:1080"); still("end_16x9.png", 4.5, "seg/he.mp4", "1920:1080")
d = concat(["seg/ht.mp4", "seg/h1.mp4", "seg/h2.mp4", "seg/hm.mp4", "seg/he.mp4"], "huhqueue-buffer-commit-demo-16x9.mp4")
print("16x9", round(d, 1))

# 9:16 reel (soft music, Follow pill, Follow end card)
segment(c1, C1, m1["c1_landing"] - 0.5, m1["c1_end"], "seg/v1.mp4", True)
segment(c2, C2, 3.5, m2["c2_end"], "seg/v2.mp4", True)
still("title_9x16.png", 3, "seg/vt.mp4", "1080:1920"); still("end_9x16.png", 3.5, "seg/ve.mp4", "1080:1920")
tot = 3 + (m1["c1_end"] - m1["c1_landing"] + 0.5) + (m2["c2_end"] - 3.5) + 3.5 + 1
run(["python3", "../huhbyte-repo/code/music/make_cinematic.py", "seg/music.wav", f"{tot:.1f}", "3"])
d = concat(["seg/vt.mp4", "seg/v1.mp4", "seg/v2.mp4", "seg/ve.mp4"], "huhqueue-buffer-commit-reel-9x16.mp4", audio="seg/music.wav")
print("9x16", round(d, 1))
