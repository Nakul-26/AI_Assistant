param(
    [double]$TimeoutSeconds = 8,
    [double]$BabbleTimeoutSeconds = 3,
    [double]$EndSilenceTimeoutSeconds = 0.8,
    [string]$Culture = "en-US",
    [string]$Choices = ""
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech

try {
    $recognizer = New-Object System.Speech.Recognition.SpeechRecognitionEngine([System.Globalization.CultureInfo]::GetCultureInfo($Culture))
}
catch {
    $recognizer = New-Object System.Speech.Recognition.SpeechRecognitionEngine
}

try {
    $choiceList = @()
    if (-not [string]::IsNullOrWhiteSpace($Choices)) {
        $choiceList = $Choices.Split("||", [System.StringSplitOptions]::RemoveEmptyEntries)
    }

    if ($choiceList.Count -gt 0) {
        $choicesBuilder = New-Object System.Speech.Recognition.Choices
        foreach ($choice in $choiceList) {
            if (-not [string]::IsNullOrWhiteSpace($choice)) {
                [void]$choicesBuilder.Add($choice)
            }
        }

        $grammarBuilder = New-Object System.Speech.Recognition.GrammarBuilder
        [void]$grammarBuilder.Append($choicesBuilder)
        $recognizer.LoadGrammar((New-Object System.Speech.Recognition.Grammar($grammarBuilder)))
    }
    else {
        $recognizer.LoadGrammar((New-Object System.Speech.Recognition.DictationGrammar))
    }

    $recognizer.BabbleTimeout = [TimeSpan]::FromSeconds($BabbleTimeoutSeconds)
    $recognizer.EndSilenceTimeout = [TimeSpan]::FromSeconds($EndSilenceTimeoutSeconds)
    $recognizer.EndSilenceTimeoutAmbiguous = [TimeSpan]::FromSeconds([Math]::Max($EndSilenceTimeoutSeconds, 1.0))
    $recognizer.SetInputToDefaultAudioDevice()

    $result = $recognizer.Recognize([TimeSpan]::FromSeconds($TimeoutSeconds))
    if ($null -ne $result -and $result.Text) {
        [Console]::Out.Write($result.Text)
    }
}
finally {
    if ($null -ne $recognizer) {
        $recognizer.Dispose()
    }
}
