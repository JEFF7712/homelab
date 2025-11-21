from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
def root():
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8" />
        <title>api.rupan.dev</title>
        <style>
            body {
                font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
                max-width: 700px;
                margin: 40px auto;
                padding: 0 16px;
                line-height: 1.6;
            }
            code {
                background: #f4f4f4;
                padding: 2px 4px;
                border-radius: 4px;
            }
            .card {
                border: 1px solid #e0e0e0;
                border-radius: 8px;
                padding: 16px 20px;
                margin-top: 16px;
            }
        </style>
    </head>
    <body>
        <h1>api.rupan.dev</h1>
        <p>This is a simple API hosted on my homelab.</p>

        <div class="card">
            <h2>Available endpoints</h2>
            <ul>
                <li>
                    <code>GET /name</code>  
                    returns my name as JSON:
                    <pre>{
  "name": "Rupan"
}</pre>
                </li>
            </ul>
        </div>

        <div class="card">
            <h2>Auto generated docs</h2>
            <p>
                FastAPI automatically exposes interactive documentation:
            </p>
            <ul>
                <li><a href="/docs">/docs</a> (Swagger UI)</li>
                <li><a href="/redoc">/redoc</a> (ReDoc)</li>
            </ul>
        </div>

        <p style="margin-top: 32px; font-size: 0.9em; color: #666;">
            Served by a FastAPI app in Docker behind a Cloudflare Tunnel.
        </p>
    </body>
    </html>
    """


@app.get("/name")
def get_name():
    return {"name": "Rupan"}
