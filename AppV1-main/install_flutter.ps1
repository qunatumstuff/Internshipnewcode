Write-Host '1. Installing Git...'
winget install --id Git.Git -e --silent --accept-package-agreements --accept-source-agreements

Write-Host '2. Downloading Flutter...'
New-Item -ItemType Directory -Force -Path "C:\src"
Invoke-WebRequest -Uri "https://storage.googleapis.com/flutter_infra_release/releases/stable/windows/flutter_windows_3.41.6-stable.zip" -OutFile "C:\src\flutter.zip"

Write-Host '3. Extracting Flutter to C:\src\flutter...'
# This step might take a few minutes depending on your computer's speed.
Expand-Archive -Path "C:\src\flutter.zip" -DestinationPath "C:\src" -Force
Remove-Item "C:\src\flutter.zip"

Write-Host '4. Adding Flutter to System PATH...'
$oldPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$flutterBin = 'C:\src\flutter\bin'

if ($oldPath -notmatch [regex]::Escape($flutterBin)) {
    $newPath = $oldPath + ';' + $flutterBin
    [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')
}

Write-Host 'Installation completed! IMPORTANT: Please restart your terminal/IDE for the PATH changes to take effect.'
