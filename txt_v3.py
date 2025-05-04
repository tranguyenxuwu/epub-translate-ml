import os
import logging
from pathlib import Path
from typing import List, Dict, Tuple
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Image, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from PIL import Image as PILImage

# Set up logging with UTF-8 support
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    encoding='utf-8'
)
logger = logging.getLogger(__name__)

class PDFBuilder:
    PAGE_WIDTH = 612
    PAGE_HEIGHT = 792
    MARGIN = 72
    MAX_IMAGE_WIDTH = PAGE_WIDTH - (2 * MARGIN)
    MAX_IMAGE_HEIGHT = PAGE_HEIGHT - (2 * MARGIN)

    def __init__(self, input_dir: str):
        """Initialize the PDF builder with input directory."""
        self.input_dir = Path(input_dir)
        self._register_fonts()
        self.styles = self._create_styles()
        self.content_types = self._define_content_types()

    def _register_fonts(self):
        """Register DejaVu Sans font for Vietnamese support."""
        try:
            font_path = self.input_dir / 'fonts' / 'DejaVuSans.ttf'
            if not font_path.exists():
                raise FileNotFoundError(f"Font file not found: {font_path}")
            pdfmetrics.registerFont(TTFont('DejaVuSans', str(font_path)))
        except Exception as e:
            logger.error(f"Error registering DejaVuSans font: {e}")
            raise

    def _create_styles(self) -> Dict:
        """
        Create and return custom styles with DejaVu Sans font.
        Returns a StyleSheet dictionary containing all custom styles.
        """
        styles = getSampleStyleSheet()
        
        # Define common style properties
        base_style_props = {
            'fontName': 'DejaVuSans',
            'alignment': TA_JUSTIFY,
            'leading': lambda fontSize: int(fontSize * 1.2),  # Dynamic leading based on font size
        }
        
        # Define style configurations
        style_configs = {
            'Title': {
                'fontSize': 24,
                'textColor': colors.HexColor('#2C3E50'),
                'spaceAfter': 30,
                'alignment': TA_JUSTIFY,
            },
            'CustomNormal': {
                'parent': styles['Normal'],
                'fontSize': 11,
                'spaceAfter': 12,
            },
            'CustomHeading1': {  # Changed from 'Heading1' to 'CustomHeading1'
                'fontSize': 18,
                'textColor': colors.HexColor('#34495E'),
                'spaceAfter': 16,
            },
            'CustomHeading2': {  # Changed from 'Heading2' to 'CustomHeading2'
                'fontSize': 14,
                'textColor': colors.HexColor('#2980B9'),
                'spaceAfter': 12,
            }
        }
        
        # Update existing Title style
        for key, value in {**base_style_props, **style_configs['Title']}.items():
            if key == 'leading':
                setattr(styles['Title'], key, value(style_configs['Title']['fontSize']))
            else:
                setattr(styles['Title'], key, value)
        
        # Create new styles
        for style_name, config in style_configs.items():
            if style_name == 'Title':
                continue  # Skip Title as it's already handled
                
            style_props = {**base_style_props, **config}
            parent = style_props.pop('parent', None)
            
            # Calculate leading based on fontSize if not explicitly set
            if 'leading' in style_props and callable(style_props['leading']):
                style_props['leading'] = style_props['leading'](style_props['fontSize'])
                
            styles.add(
                ParagraphStyle(
                    name=style_name,
                    parent=parent,
                    **style_props
                )
            )
        
        return styles

    def _define_content_types(self) -> Dict[str, Tuple[str, int]]:
        """Define content types and their corresponding styles."""
        return {
            "TITLE: ": ('Title', 24),
            "H1: ": ('Heading1', 16),
            "H2: ": ('Heading2', 12),
            "P: ": ('CustomNormal', 8)
        }

    def _scale_image(self, image_path: Path) -> Tuple[float, float]:
        """Calculate appropriate image dimensions maintaining aspect ratio."""
        SAFE_WIDTH = self.MAX_IMAGE_WIDTH - 12
        SAFE_HEIGHT = self.MAX_IMAGE_HEIGHT - 12
        
        with PILImage.open(image_path) as img:
            orig_width, orig_height = img.size
            
        aspect = orig_width / orig_height
        new_width = min(SAFE_WIDTH, orig_width)
        new_height = new_width / aspect
        
        if new_height > SAFE_HEIGHT:
            new_height = SAFE_HEIGHT
            new_width = new_height * aspect
            
        return min(new_width, SAFE_WIDTH), min(new_height, SAFE_HEIGHT)

    def _process_image(self, img_path: str) -> List:
        """Process image and return story elements."""
        elements = []
        full_path = self.input_dir / 'images' / Path(img_path).name
        
        try:
            if not full_path.exists():
                logger.warning(f"Image not found: {full_path}")
                return elements

            width, height = self._scale_image(full_path)
            img = Image(str(full_path), width=width, height=height)
            elements.extend([img, Spacer(1, 12)])
            
        except Exception as e:
            logger.error(f"Error processing image {full_path}: {e}")
            
        return elements

    def _process_content(self, content_lines: List[str]) -> List:
        """Process content lines and return story elements."""
        story = []
        
        for line in content_lines:
            line = line.strip()
            if not line:
                continue
                
            try:
                if line.startswith("===== "):
                    section = line.strip("=").strip()
                    story.extend([
                        Paragraph(section, self.styles['Heading1']),
                        Spacer(1, 16)
                    ])
                    
                elif line.startswith("IMAGE: "):
                    img_path = line.split("IMAGE: ", 1)[1].strip()
                    story.extend(self._process_image(img_path))
                    
                else:
                    for prefix, (style_name, spacing) in self.content_types.items():
                        if line.startswith(prefix):
                            text = line.split(prefix, 1)[1]
                            story.extend([
                                Paragraph(text, self.styles[style_name]),
                                Spacer(1, spacing)
                            ])
                            break
                    else:
                        story.extend([
                            Paragraph(line, self.styles['CustomNormal']),
                            Spacer(1, 8)
                        ])
                        
            except Exception as e:
                logger.error(f"Error processing line '{line}': {e}")
                
        return story

    def build(self, output_filename: str = 'output.pdf') -> bool:
        """Build the PDF from content file."""
        try:
            if not self.input_dir.exists():
                raise FileNotFoundError(f"Input directory not found: {self.input_dir}")

            content_file = self.input_dir / 'tl.txt'
            if not content_file.exists():
                raise FileNotFoundError(f"Content file not found: {content_file}")

            output_path = self.input_dir / output_filename
            output_path.parent.mkdir(parents=True, exist_ok=True)

            logger.info(f"Reading content from {content_file}")
            with open(content_file, 'r', encoding='utf-8') as f:
                content_lines = f.readlines()

            doc = SimpleDocTemplate(
                str(output_path),
                pagesize=letter,
                leftMargin=self.MARGIN,
                rightMargin=self.MARGIN,
                topMargin=self.MARGIN,
                bottomMargin=self.MARGIN
            )

            story = self._process_content(content_lines)
            doc.build(story)
            
            logger.info(f"PDF successfully created at {output_path}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to build PDF: {e}")
            return False

def main():
    """Main function to run the PDF builder."""
    script_dir = Path(__file__).parent
    builder = PDFBuilder(script_dir)
    
    if builder.build():
        logger.info("PDF generation completed successfully")
    else:
        logger.error("PDF generation failed")

if __name__ == "__main__":
    main()