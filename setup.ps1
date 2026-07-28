# =====================================================================
# 自宅PC開発環境セットアップスクリプト(Windows / PowerShell)
# =====================================================================

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==== $Message ====" -ForegroundColor Cyan
}

function Test-CommandExists {
    param([string]$Command)
    return [bool](Get-Command $Command -ErrorAction SilentlyContinue)
}

# ---------------------------------------------------------------------
# 0. Winget の存在確認
# ---------------------------------------------------------------------
Write-Step "Winget(パッケージマネージャー)の確認"

if (-not (Test-CommandExists "winget")) {
    Write-Host "wingetが見つかりません。" -ForegroundColor Red
    Write-Host "Microsoft Store を開いて「アプリ インストーラー」を更新/インストールしてから、再度このスクリプトを実行してください。" -ForegroundColor Yellow
    exit 1
}
Write-Host "wingetが利用可能です。" -ForegroundColor Green

# ---------------------------------------------------------------------
# 1. Python のインストール
# ---------------------------------------------------------------------
Write-Step "Python のインストール"

if (Test-CommandExists "python") {
    $pyVersion = python --version
    Write-Host "Pythonはすでにインストールされています: $pyVersion" -ForegroundColor Green
} else {
    Write-Host "Pythonをインストールします..."
    winget install --id Python.Python.3.12 -e --source winget --accept-source-agreements --accept-package-agreements --override "/passive PrependPath=1"
    Write-Host "Pythonのインストールが完了しました。" -ForegroundColor Green
    Write-Host "※ このターミナルではまだ反映されない場合があります。完了後にターミナルを開き直してください。" -ForegroundColor Yellow
}

# ---------------------------------------------------------------------
# 2. Git のインストール
# ---------------------------------------------------------------------
Write-Step "Git のインストール"

if (Test-CommandExists "git") {
    $gitVersion = git --version
    Write-Host "Gitはすでにインストールされています: $gitVersion" -ForegroundColor Green
} else {
    Write-Host "Gitをインストールします..."
    winget install --id Git.Git -e --source winget --accept-source-agreements --accept-package-agreements
    Write-Host "Gitのインストールが完了しました。" -ForegroundColor Green
}

# ---------------------------------------------------------------------
# 3. Visual Studio Code のインストール
# ---------------------------------------------------------------------
Write-Step "Visual Studio Code のインストール"

if (Test-CommandExists "code") {
    Write-Host "VS Codeはすでにインストールされています。" -ForegroundColor Green
} else {
    Write-Host "VS Codeをインストールします..."
    winget install --id Microsoft.VisualStudioCode -e --source winget --accept-source-agreements --accept-package-agreements
    Write-Host "VS Codeのインストールが完了しました。" -ForegroundColor Green
}

# ---------------------------------------------------------------------
# 4. VS Code 拡張機能のインストール
# ---------------------------------------------------------------------
Write-Step "VS Code 拡張機能のインストール"

Write-Host "変更を反映するため、ターミナルを一度開き直してから続行する場合があります。" -ForegroundColor Yellow
Write-Host "'code' コマンドが使えるか確認しています..."

Start-Sleep -Seconds 3

if (Test-CommandExists "code") {
    $extensions = @(
        "ms-python.python",            # Python
        "ms-python.vscode-pylance",    # Pylance(型補完・高速化)
        "eamodio.gitlens",              # GitLens(Git履歴の可視化)
        "esbenp.prettier-vscode",      # コード整形
        "yzhang.markdown-all-in-one"   # Markdown編集支援
    )

    foreach ($ext in $extensions) {
        Write-Host "拡張機能をインストール中: $ext"
        code --install-extension $ext --force
    }
    Write-Host "VS Code拡張機能のインストールが完了しました。" -ForegroundColor Green
} else {
    Write-Host "'code' コマンドがまだ認識されません。" -ForegroundColor Yellow
    Write-Host "このスクリプト完了後、一度PCを再起動してから以下を手動で実行してください:" -ForegroundColor Yellow
    Write-Host "  code --install-extension ms-python.python"
    Write-Host "  code --install-extension ms-python.vscode-pylance"
    Write-Host "  code --install-extension eamodio.gitlens"
    Write-Host "  code --install-extension esbenp.prettier-vscode"
    Write-Host "  code --install-extension yzhang.markdown-all-in-one"
}

# ---------------------------------------------------------------------
# 5. Python ライブラリのインストール(AI動向ダイジェスト用)
# ---------------------------------------------------------------------
Write-Step "Python ライブラリのインストール"

if (Test-CommandExists "pip") {
    Write-Host "pipをアップグレードしています..."
    python -m pip install --upgrade pip

    Write-Host "必要なライブラリをインストールしています..."
    python -m pip install anthropic feedparser requests

    Write-Host "ライブラリのインストールが完了しました。" -ForegroundColor Green
} else {
    Write-Host "pipが見つかりません。Pythonのインストール後、ターミナルを開き直してから再実行してください。" -ForegroundColor Yellow
}

# ---------------------------------------------------------------------
# 6. Git 初期設定(ユーザー名・メールアドレス)
# ---------------------------------------------------------------------
Write-Step "Git の初期設定"

$currentName = git config --global user.name 2>$null
$currentEmail = git config --global user.email 2>$null

if ($currentName -and $currentEmail) {
    Write-Host "Gitの設定はすでに完了しています: $currentName <$currentEmail>" -ForegroundColor Green
} else {
    Write-Host "Gitのユーザー情報が未設定です。" -ForegroundColor Yellow
    $inputName = Read-Host "Gitに登録する名前を入力してください(例: Taro Yamada)"
    $inputEmail = Read-Host "Gitに登録するメールアドレスを入力してください(GitHubのアカウントと同じものを推奨)"

    git config --global user.name "$inputName"
    git config --global user.email "$inputEmail"
    Write-Host "Gitの初期設定が完了しました。" -ForegroundColor Green
}

# ---------------------------------------------------------------------
# 完了
# ---------------------------------------------------------------------
Write-Step "セットアップ完了"

Write-Host ""
Write-Host "以下がインストール・設定されました:" -ForegroundColor Green
Write-Host "  - Python"
Write-Host "  - Git"
Write-Host "  - Visual Studio Code"
Write-Host "  - VS Code拡張機能(Python, Pylance, GitLens など)"
Write-Host "  - Pythonライブラリ(anthropic, feedparser, requests)"
Write-Host ""
Write-Host "反映のため、一度PCを再起動することをおすすめします。" -ForegroundColor Yellow
Write-Host "再起動後、VS Codeを開いて動作確認してください。" -ForegroundColor Yellow
