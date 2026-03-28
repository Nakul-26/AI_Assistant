param(
    [Parameter(Mandatory = $true)]
    [string]$Text,
    [string]$Voice = "",
    [int]$Rate = 0
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech

$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer

try {
    if ($Voice) {
        $synth.SelectVoice($Voice)
    }

    $synth.Rate = $Rate
    $synth.Speak($Text)
}
finally {
    if ($null -ne $synth) {
        $synth.Dispose()
    }
}
