# Deploy To Render (Always-On URL)

## 1) Push project to GitHub
Run in project root:

```powershell
git init
git add .
git commit -m "Prepare Render deployment"
git branch -M main
git remote add origin <YOUR_GITHUB_REPO_URL>
git push -u origin main
```

## 2) Create Render web service
1. Login to Render.
2. Click New + -> Web Service.
3. Select your GitHub repository.
4. Render reads `render.yaml` automatically.
5. Click Create Web Service.

## 3) Wait for first deploy
- Build logs should show package install and Streamlit start.
- After success, open the generated URL.

## 4) Daily usage
- Edit code locally.
- Commit + push to `main`.
- Render auto-deploys the new version.

## Notes
- This project currently asks user to input DeepSeek API key in sidebar.
- `finance.db` is local ephemeral storage; for long-term multi-user data, migrate to PostgreSQL later.
