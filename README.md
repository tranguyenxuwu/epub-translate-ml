# Translate Your Favorite Light Novel to Your Favorite Language

(Currently supports Vietnamese)

This project leverages machine learning to translate EPUB files . Whether you're a light novel enthusiast or simply want to explore stories in your preferred language, this tool provides an efficient and user-friendly workflow.

## Features

- Translate EPUB files to other languages (initially Vietnamese).
- Outputs translations in well-formatted PDF files.
- Utilizes Google Colab for GPU acceleration.
- Simple integration with ngrok for API handling.

## How to Use

0. **Activate virtual environment**
   install requirements (or create venv)
   `pip install -r requirements.txt`

1. **Create an ngrok account**  
   Sign up at [ngrok](https://ngrok.com/) and get your API key.

2. **Open Google Colab**

   - Go to [Google Colab](https://colab.research.google.com/).
   - Upload the Jupyter notebook provided in this repository.

3. **Setup Colab Runtime**

   - Change the runtime to **GPU** for better performance.  
     Go to **Runtime** > **Change runtime type** > Select **GPU**, then **Connect**.

4. **Configure ngrok**

   - Paste your ngrok API key in the designated cell in the notebook.

5. **Run the Notebook**

   - Run all cells in the notebook step by step.

6. **Setup API**

   - Copy the ngrok link generated in the last cell of the notebook.
   - Paste the link into `translate_to_txt.py`.

7. **Customize Input/Output**

   - Modify the input/output file paths in the script.
   - Run the script to see the translation results.

8. **Build Epub File**
   - enter txt translated file and run `txt_to_epub.py`

Contribute

We welcome feedback, ideas, and contributions! Feel free to submit issues or pull requests to improve this project.

Good Luck!

Enjoy translating your favorite light novels and exploring new stories in your preferred language.
