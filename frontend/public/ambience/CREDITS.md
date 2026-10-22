# 环境音的来源

这里的环境音都来自 [Freesound](https://freesound.org/)，下载时筛选的许可证是 Creative Commons 0（CC0），可以自由使用、不需要署名。
原始文件在仓库根目录的 `corpus/ambience-originals/`（不进仓库）；这里的是转成 44.1 kHz 立体声 160 kbps 的 mp3、
响度统一到 -28 LUFS、最长截到 3 分钟的版本：

```sh
ffmpeg -i <原始文件> -t 180 -ac 2 -ar 44100 -af "loudnorm=I=-28:TP=-2:LRA=11" -b:a 160k <文件名>.mp3
```

| 文件 | 原始文件 | Freesound 页面 |
| --- | --- | --- |
| rain-1.mp3 | rain.wav | （待补） |
| rain-2.mp3 | rain2.flac | （待补） |
| thunder-1.mp3 | distant thunder.aiff | （待补） |
| thunder-2.mp3 | distant thunder2.wav | （待补） |
| fireplace-1.mp3 | fireplace crackling.wav | （待补） |
| fireplace-2.mp3 | fireplace crackling2.m4a | （待补） |
| waves-1.mp3 | calm ocean waves.flac | （待补） |
| waves-2.mp3 | calm ocean waves2.wav | （待补） |
| summer-night-1.mp3 | crickets night.wav | （待补） |
| clock-1.mp3 | clock ticking.wav | （待补） |
| clock-2.mp3 | clock ticking2.wav | （待补） |
| pages.mp3 | page turn.mp3 | （待补） |
| cafe-1.mp3 | cafe ambience quiet.flac | （待补） |
| bath-1.mp3 | 自己准备：浴室的环境音（水声、回声），她在浴缸里接电话时垫在下面（GLOSSARY.md「洗澡」）；没有这个文件就只有回响 | — |
