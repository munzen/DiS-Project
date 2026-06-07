from website import create_app # imports function create_app from the __init__.py inwebsite/
app = create_app()
if __name__ == '__main__': # only run webserver when main.py runs (not when importing!)
    app.run(host='0.0.0.0', port=3025, debug=True)

