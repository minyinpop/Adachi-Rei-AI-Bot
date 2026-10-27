# OpenUtau headless renderer prototype

This small Windows console host calls the OpenUtau 0.1.565 core mixdown renderer
without opening the OpenUtau window or driving the mouse. The project source and
build output live here. The executable uses the installed OpenUtau assemblies and
the existing `%USERPROFILE%\Documents\OpenUtau` data.

Build from this directory:

```powershell
dotnet build .\OpenUtauRenderer.csproj
```

Render a saved project:

```powershell
& '.\bin\OpenUtauRenderer.exe' `
  'D:\Adachi-Rei-AI-Bot\openutau_adapter\output\input.ustx' `
  'D:\Adachi-Rei-AI-Bot\openutau_adapter\output\rendered.wav'
```

It requires the current OpenUtau installation and voicebanks to remain
available, and it is tied to the installed OpenUtau core API. It accepts saved
`.ustx` projects and renders them without starting the OpenUtau UI.

The Python adapter reads `renderer_exe` from `openutau_adapter/openutau_configs.json`
and calls this executable for both speech mixdown and song excerpts. Song excerpts
are cut from the singer-configured `.ustx` projects in `openutau_adapter/songs/`.
