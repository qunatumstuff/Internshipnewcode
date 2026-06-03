Write-Host '1. Setting up Environment Variables in current session...'
$env:Path = [System.Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [System.Environment]::GetEnvironmentVariable('Path','User')

Write-Host '2. Checking for Node.js...'
if (!(Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Host 'Node.js not found. Installing Node.js...'
    winget install --id OpenJS.NodeJS -e --silent --accept-package-agreements --accept-source-agreements
    $env:Path = [System.Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [System.Environment]::GetEnvironmentVariable('Path','User')
}

Write-Host '3. Ensuring Flutter is correctly extracted...'
if (!(Test-Path 'C:\src\flutter\bin\flutter.bat')) {
    if (Test-Path 'C:\src\flutter.zip') {
        Write-Host 'Extracting flutter.zip (using optimized tar.exe)...'
        tar.exe -xf 'C:\src\flutter.zip' -C 'C:\src'
    } else {
        Write-Host 'Flutter zip missing, please run the flutter download tool again.'
        exit
    }
}
if ($env:Path -notmatch 'C:\\src\\flutter\\bin') {
    $env:Path += ';C:\src\flutter\bin'
}

Write-Host '4. Navigating to the correct project folder...'
Set-Location 'C:\Users\Dominic\Downloads\AppV1-main\AppV1-main'

Write-Host '5. Downloading Flutter dependencies...'
flutter config --no-analytics
flutter precache
flutter pub get

Write-Host '6. Building Flutter Web App...'
flutter build web --release

Write-Host '7. Deploying to Node server directory...'
# Create Public folder if it does not exist
if (!(Test-Path 'roboas\Public')) {
    New-Item -ItemType Directory -Force -Path 'roboas\Public' | Out-Null
} else {
    # Exclude models and ort subdirectories to preserve downloaded runtimes and custom wakewords
    Get-ChildItem -Path roboas\Public -Exclude models, ort | Remove-Item -Recurse -Force
}
Copy-Item -Recurse -Force build\web\* roboas\Public\

Write-Host '8. Starting Node Server...'
Set-Location 'roboas'
if (!(Test-Path 'node_modules')) {
    Write-Host 'Running npm install in roboas folder...'
    npm install
}
Write-Host 'Server is starting!'
node server.js
