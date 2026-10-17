from .server import build_server

if __name__ == "__main__":
    build_server().run(transport="stdio")
