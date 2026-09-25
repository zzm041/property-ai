$ErrorActionPreference='Stop'
$target='D:\property-ai\tmp\selfhost-ai-runtime\compat-027'
if(-not(Test-Path -LiteralPath $target)){New-Item -ItemType Directory -Path $target|Out-Null}
$wav=Join-Path $target 'repair-reference.wav'
if(Test-Path -LiteralPath $wav){throw 'Synthetic audio exists; preserve and inspect, no repeat generation'}
Add-Type -AssemblyName System.Speech
$speech=New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $speech.SelectVoice('Microsoft Huihui Desktop')
    $speech.Rate=0
    $format=New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000,[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,[System.Speech.AudioFormat.AudioChannel]::Mono)
    $speech.SetOutputToWaveFile($wav,$format)
    $speech.Speak('厨房水龙头关紧后还在滴水，没有积水，请维修人员检查。')
    $speech.SetOutputToNull()
} finally {$speech.Dispose()}
[PSCustomObject]@{path=$wav;sha256=(Get-FileHash -LiteralPath $wav -Algorithm SHA256).Hash;reference='厨房水龙头关紧后还在滴水，没有积水，请维修人员检查。';source='Existing local Windows Microsoft Huihui Desktop; no network, no resident voice';sample_rate=16000;channels=1;bits=16}|ConvertTo-Json
