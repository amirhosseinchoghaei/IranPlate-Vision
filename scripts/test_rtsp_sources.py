import cv2

TEST_URLS = [
    'rtsp://wowzaec2demo.streamlock.net/vod/mp4:BigBuckBunny_115k.mov',
    'rtsp://184.72.239.149/vod/mp4:BigBuckBunny_175k.mov',
]

for url in TEST_URLS:
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    ok, frame = cap.read()
    print(f'{url}\n  opened={cap.isOpened()} read={ok} shape={None if frame is None else frame.shape}')
    cap.release()
