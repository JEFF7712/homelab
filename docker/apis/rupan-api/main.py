from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def root():
    return {"message": "Hello from Rupan API"}


@app.get("/name")
def get_name():
    return {"name": "Rupan"}
