Write-Host "Clearing ChromaDB cache..." -ForegroundColor Yellow

if (Test-Path ".\chroma_store") {
    try {
        Remove-Item -Recurse -Force .\chroma_store -ErrorAction Stop
        Write-Host "✅ Cache cleared successfully!" -ForegroundColor Green
    } catch {
        Write-Host "❌ Failed to clear cache. Make sure you stop your FastAPI / Uvicorn backend (Ctrl+C) before running this!" -ForegroundColor Red
        Write-Host $_.Exception.Message -ForegroundColor Red
    }
} else {
    Write-Host "✅ Cache is already empty." -ForegroundColor Green
}
