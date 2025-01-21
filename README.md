# epub-translate-ml

A machine learning project for translating EPUB files to PDF.

## How to Use

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
   - Paste the link into `api-callv3.py`.

7. **Customize Input/Output**  
   - Modify the input/output file paths in the script.
   - Run the script to see the translation results.

## Good Luck!

Enjoy translating your EPUB files into high-quality PDF documents.
