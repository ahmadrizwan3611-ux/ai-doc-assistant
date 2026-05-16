from flask import Flask, request, jsonify
from flask_cors import CORS
import ast
import re
import os
import logging
from dotenv import load_dotenv
from openai import OpenAI

app = Flask(__name__)

# Production-safe configuration
app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MAX_CONTENT_LENGTH", 5 * 1024 * 1024))
MAX_CODE_CHARS = int(os.getenv("MAX_CODE_CHARS", "1200000"))

allowed_origins = os.getenv(
    "ALLOWED_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:3000"
).split(",")

CORS(
    app,
    resources={r"/*": {"origins": [origin.strip() for origin in allowed_origins if origin.strip()]}},
    supports_credentials=False,
)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def is_ai_enabled():
    api_key = os.getenv("OPENAI_API_KEY")
    if api_key:
        return True
    return False


def generate_ai_documentation(code, file_name=""):
    try:
        prompt = f"""
You are a senior software architect and technical documentation engineer.

Analyze this source code and generate professional documentation.

Include:
- Project/file purpose
- Function explanations
- Architecture overview
- API routes
- Important logic
- Dependencies
- Security observations
- Improvements
- Clean formatting

File Name:
{file_name}

Code:
{code}
"""

        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {
                    "role": "system",
                    "content": "You generate professional software documentation.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=3000,
        )

        return {
            "success": True,
            "doc": response.choices[0].message.content,
            "error": "",
        }

    except Exception as error:
        return {
            "success": False,
            "doc": "",
            "error": str(error),
        }


def detect_return_type(node):
    if isinstance(node, ast.Constant):
        return type(node.value).__name__

    if isinstance(node, ast.BinOp):
        return "number"

    if isinstance(node, ast.Call):
        return "object / result of function call"

    if isinstance(node, ast.Dict):
        return "dict"

    if isinstance(node, ast.List):
        return "list"

    if isinstance(node, ast.Tuple):
        return "tuple"

    return "value"


def generate_description(name, args):
    clean_name = name.replace("_", " ")

    if name.startswith("calculate_"):
        return (
            "The `"
            + name
            + "` function calculates "
            + clean_name.replace("calculate ", "")
            + " using the provided input values."
        )

    if name.startswith("get_"):
        return (
            "The `"
            + name
            + "` function retrieves "
            + clean_name.replace("get ", "")
            + " based on the provided input."
        )

    if name.startswith("create_"):
        return (
            "The `"
            + name
            + "` function creates "
            + clean_name.replace("create ", "")
            + " using the given data."
        )

    if name.startswith("update_"):
        return (
            "The `"
            + name
            + "` function updates "
            + clean_name.replace("update ", "")
            + " based on the provided values."
        )

    if name.startswith("delete_"):
        return (
            "The `"
            + name
            + "` function deletes or removes "
            + clean_name.replace("delete ", "")
            + "."
        )

    if name.startswith("is_"):
        return (
            "The `"
            + name
            + "` function checks whether "
            + clean_name.replace("is ", "")
            + " is true or false."
        )

    if "discount" in name:
        return (
            "The `"
            + name
            + "` function calculates the final price after applying a discount."
        )

    if "divide" in name:
        return (
            "The `"
            + name
            + "` function divides one number by another and handles possible division-related cases."
        )

    if "add" in name:
        return "The `" + name + "` function adds input values and returns their sum."

    return (
        "The `"
        + name
        + "` function performs the operation suggested by its name: "
        + clean_name
        + "."
    )


def explain_parameter(param_name):
    clean = param_name.replace("_", " ")

    if param_name in ["price", "amount", "total", "cost"]:
        return "Original numeric value used in the calculation."

    if param_name in ["discount", "discount_rate"]:
        return "Discount percentage or discount value applied to the price."

    if param_name in ["a", "b", "x", "y", "num1", "num2"]:
        return "Numeric input value used by the function."

    if param_name in ["name", "username", "user_name"]:
        return "Name or username value used by the function."

    if param_name in ["email", "user_email"]:
        return "Email address value used by the function."

    if param_name in ["id", "user_id", "product_id"]:
        return "Unique identifier used to find or process a record."

    if param_name in ["items", "products", "data", "records"]:
        return "Collection of values used by the function."

    if param_name in ["text", "message", "content"]:
        return "Text input processed by the function."

    return "Input value related to " + clean + "."


def explain_return(name, returns):
    if "discount" in name:
        return "- `number or string`: Returns the final discounted price. If the discount is invalid, it may return an error message."

    if "divide" in name:
        return "- `number or string`: Returns the division result. If division is not possible, it may return an error message."

    if "add" in name:
        return "- `number`: Returns the sum of the input values."

    if "calculate" in name:
        return "- `number`: Returns the calculated result."

    if "get" in name:
        return "- `value/object`: Returns the requested data or object."

    if "is" in name:
        return "- `boolean`: Returns True or False based on the condition."

    if returns:
        return (
            "- `" + returns[0] + "`: Returns the final result produced by the function."
        )

    return "- `None`: This function does not clearly return a value."


def example_value_for_param(param_name):
    if param_name in ["price", "amount", "total", "cost"]:
        return "1000"

    if param_name in ["discount", "discount_rate"]:
        return "10"

    if param_name in ["a", "x", "num1"]:
        return "10"

    if param_name in ["b", "y", "num2"]:
        return "2"

    if param_name in ["name", "username", "user_name"]:
        return '"Ahmad"'

    if param_name in ["email", "user_email"]:
        return '"user@example.com"'

    if param_name in ["id", "user_id", "product_id"]:
        return "1"

    if param_name in ["text", "message", "content"]:
        return '"Hello world"'

    if param_name in ["items", "products", "data", "records"]:
        return "[1, 2, 3]"

    return "1"


def generate_logic_summary(func):
    logic_lines = []

    for node in ast.walk(func):
        if isinstance(node, ast.If):
            condition = ast.unparse(node.test)
            logic_lines.append("- Checks condition: `" + condition + "`")

        if isinstance(node, ast.Assign):
            targets = []
            for target in node.targets:
                if isinstance(target, ast.Name):
                    targets.append(target.id)

            if targets:
                logic_lines.append("- Assigns value to: `" + ", ".join(targets) + "`")

        if isinstance(node, ast.Return):
            if node.value:
                logic_lines.append("- Returns: `" + ast.unparse(node.value) + "`")
            else:
                logic_lines.append("- Returns without a value.")

    if logic_lines:
        return "\n".join(logic_lines)

    return "- No detailed internal logic detected."


def explain_condition(condition):
    if "< 0" in condition:
        variable = condition.replace("< 0", "").strip()
        return "Checks whether `" + variable + "` is negative."

    if "== 0" in condition:
        variable = condition.replace("== 0", "").strip()
        return "Checks whether `" + variable + "` is zero."

    if "!= 0" in condition:
        variable = condition.replace("!= 0", "").strip()
        return "Checks whether `" + variable + "` is not zero."

    if "is None" in condition:
        variable = condition.replace("is None", "").strip()
        return "Checks whether `" + variable + "` is missing or None."

    if "not " in condition:
        variable = condition.replace("not ", "").strip()
        return "Checks whether `" + variable + "` is empty, missing, or false."

    return "Handles condition: `" + condition + "`"


def generate_function_doc(func):
    name = func.name
    args = [arg.arg for arg in func.args.args]

    returns = []
    edge_cases = []

    for node in ast.walk(func):
        if isinstance(node, ast.Return) and node.value:
            returns.append(detect_return_type(node.value))

        if isinstance(node, ast.If):
            condition = ast.unparse(node.test)
            edge_cases.append(explain_condition(condition))

    if args:
        params_lines = []
        for arg in args:
            params_lines.append("- `" + arg + "`: " + explain_parameter(arg))
        params_text = "\n".join(params_lines)
    else:
        params_text = "- This function does not take any parameters."

    if returns:
        return_type = returns[0]
    else:
        return_type = "None / not clearly defined"

    if edge_cases:
        edge_text = "\n".join(["- " + edge for edge in edge_cases])
    else:
        edge_text = "- No special edge cases detected."

    example_args = ", ".join([example_value_for_param(arg) for arg in args])

    if args:
        example = name + "(" + example_args + ")"
    else:
        example = name + "()"

    doc_lines = [
        "## `" + name + "` Function Documentation",
        "",
        "### Function Description",
        generate_description(name, args),
        "",
        "### Parameters",
        params_text,
        "",
        "### Logic Summary",
        generate_logic_summary(func),
        "",
        "### Returns",
        explain_return(name, returns),
        "",
        "### Edge Cases",
        edge_text,
        "",
        "### Example",
        "```python",
        example,
        "```",
    ]

    return "\n".join(doc_lines)


def detect_language(code, file_name=""):
    lower_code = code.lower().strip()
    lower_file = file_name.lower().strip()

    if lower_file.endswith(".py"):
        return "Python"

    if lower_file.endswith(".jsx"):
        return "React"

    if lower_file.endswith(".tsx"):
        return "React / TypeScript"

    if lower_file.endswith(".ts"):
        return "TypeScript"

    if lower_file.endswith(".js"):
        return "JavaScript"

    if lower_file.endswith(".html"):
        return "HTML"

    if lower_file.endswith(".css"):
        return "CSS"

    if lower_file.endswith(".sql"):
        return "SQL"

    if lower_file.endswith(".php"):
        return "PHP"

    if lower_file.endswith(".java"):
        return "Java"

    if (
        lower_file.endswith(".cpp")
        or lower_file.endswith(".c")
        or lower_file.endswith(".h")
    ):
        return "C/C++"

    if lower_file.endswith(".cs"):
        return "C#"

    if lower_file.endswith(".kt"):
        return "Kotlin"

    if lower_file.endswith(".swift"):
        return "Swift"

    if lower_file.endswith(".dart"):
        return "Flutter"

    if "<?php" in lower_code or "echo " in lower_code:
        return "PHP"

    if "def " in code and ":" in code:
        return "Python"

    if (
        "react-native" in lower_code
        or "stylesheet.create" in lower_code
        or "from 'react-native'" in lower_code
        or 'from "react-native"' in lower_code
    ):
        return "React Native"

    if (
        "import react" in lower_code
        or "from 'react'" in lower_code
        or 'from "react"' in lower_code
        or "jsx" in lower_code
    ):
        return "React"

    if (
        "return (" in lower_code
        and "<" in code
        and ">" in code
        and ("className" in code or "</" in code)
    ):
        return "React"

    if (
        "interface " in lower_code
        or "type " in lower_code
        or ": string" in lower_code
        or ": number" in lower_code
        or ": boolean" in lower_code
    ):
        return "TypeScript"

    if (
        "function " in code
        or "console.log" in code
        or "const " in code
        or "let " in code
    ):
        return "JavaScript"

    if (
        "<html" in lower_code
        or "<div" in lower_code
        or "<body" in lower_code
        or "<!doctype html" in lower_code
    ):
        return "HTML"

    if (
        "{" in code
        and "}" in code
        and (
            "color:" in lower_code
            or "background:" in lower_code
            or "font-size:" in lower_code
        )
    ):
        return "CSS"

    if (
        "select " in lower_code
        or "insert into" in lower_code
        or "create table" in lower_code
        or "update " in lower_code
    ):
        return "SQL"

    if (
        "package:flutter" in lower_code
        or "statelesswidget" in lower_code
        or "statefulwidget" in lower_code
        or "widget build" in lower_code
    ):
        return "Flutter"

    if (
        "func " in lower_code
        or "let " in lower_code
        or "var " in lower_code
        or "import swiftui" in lower_code
        or "import uikit" in lower_code
    ):
        return "Swift"

    if (
        "fun " in lower_code
        or "val " in lower_code
        or "var " in lower_code
        or "println(" in lower_code
    ):
        return "Kotlin"

    if (
        "using system" in lower_code
        or "namespace " in lower_code
        or "console.writeline" in lower_code
    ):
        return "C#"

    if "public class" in code or "system.out.println" in lower_code:
        return "Java"

    if "#include" in code and "int main" in code:
        return "C/C++"

    return "Unknown"


def analyze_javascript(code):
    lines = code.splitlines()

    function_name = "unknown_function"
    params = []

    for line in lines:
        clean_line = line.strip()

        if clean_line.startswith("function "):
            function_part = clean_line.replace("function ", "")
            function_name = function_part.split("(")[0].strip()

            if "(" in function_part and ")" in function_part:
                params_text = function_part.split("(")[1].split(")")[0]
                params = [p.strip() for p in params_text.split(",") if p.strip()]

    if params:
        params_lines = []
        for param in params:
            params_lines.append("- `" + param + "`: Input value used by the function.")
        params_text = "\n".join(params_lines)
    else:
        params_text = "- This function does not take any parameters."

    logic_lines = []

    for line in lines:
        clean_line = line.strip()

        if clean_line.startswith("return "):
            logic_lines.append(
                "- Returns: `"
                + clean_line.replace("return ", "").replace(";", "")
                + "`"
            )

        if "console.log" in clean_line:
            logic_lines.append("- Outputs data to the console.")

        if clean_line.startswith("if "):
            logic_lines.append("- Checks a condition.")

    if logic_lines:
        logic_text = "\n".join(logic_lines)
    else:
        logic_text = "- No detailed logic detected."

    example_args = ", ".join(["1" for p in params])

    if params:
        example = function_name + "(" + example_args + ")"
    else:
        example = function_name + "()"

    doc_lines = [
        function_name + " Function Documentation",
        "",
        "Function Description",
        "The "
        + function_name
        + " function is a JavaScript function that performs logic based on the provided code.",
        "",
        "Language",
        "JavaScript",
        "",
        "Parameters",
        params_text,
        "",
        "Logic Summary",
        logic_text,
        "",
        "Example",
        example,
    ]

    return "\n".join(doc_lines)


def analyze_html(code):
    lower_code = code.lower()

    elements = []

    common_tags = [
        "html",
        "head",
        "body",
        "div",
        "section",
        "header",
        "footer",
        "nav",
        "main",
        "h1",
        "h2",
        "h3",
        "p",
        "a",
        "img",
        "form",
        "input",
        "button",
        "ul",
        "li",
        "table",
    ]

    for tag in common_tags:
        if "<" + tag in lower_code:
            elements.append(tag)

    if elements:
        elements_text = "\n".join(
            ["- `" + tag + "` element is used in this file." for tag in elements]
        )
    else:
        elements_text = "- No common HTML elements detected."

    purpose = "This HTML file defines the structure and content of a web page."

    if "<form" in lower_code:
        purpose = "This HTML file contains a form used to collect user input."

    if "<nav" in lower_code:
        purpose = "This HTML file includes navigation structure for a web page."

    if "<table" in lower_code:
        purpose = "This HTML file contains a table used to display structured data."

    doc_lines = [
        "HTML File Documentation",
        "",
        "File Description",
        purpose,
        "",
        "Language",
        "HTML",
        "",
        "Detected Elements",
        elements_text,
        "",
        "Developer Notes",
        "- This file controls the page structure.",
        "- Styling may be handled through CSS files or inline styles.",
        "- JavaScript may be connected through script tags if present.",
    ]

    return "\n".join(doc_lines)


def analyze_css(code):
    lower_code = code.lower()

    detected_features = []

    if "color:" in lower_code:
        detected_features.append("- Defines text color or element color.")

    if "background:" in lower_code or "background-color:" in lower_code:
        detected_features.append("- Defines background styling.")

    if "font-size:" in lower_code or "font-family:" in lower_code:
        detected_features.append("- Controls font styling.")

    if "margin:" in lower_code or "padding:" in lower_code:
        detected_features.append("- Controls spacing around elements.")

    if "display: flex" in lower_code:
        detected_features.append("- Uses Flexbox layout.")

    if "display: grid" in lower_code:
        detected_features.append("- Uses CSS Grid layout.")

    if "@media" in lower_code:
        detected_features.append("- Includes responsive design media queries.")

    if "border:" in lower_code or "border-radius:" in lower_code:
        detected_features.append("- Defines border or rounded corner styling.")

    if detected_features:
        features_text = "\n".join(detected_features)
    else:
        features_text = "- No common CSS styling features detected."

    selectors = []
    lines = code.splitlines()

    for line in lines:
        clean_line = line.strip()

        if clean_line.endswith("{"):
            selector = clean_line.replace("{", "").strip()
            if selector:
                selectors.append(selector)

    if selectors:
        selectors_text = "\n".join(
            ["- `" + selector + "` selector is styled." for selector in selectors]
        )
    else:
        selectors_text = "- No CSS selectors detected."

    doc_lines = [
        "CSS File Documentation",
        "",
        "File Description",
        "This CSS file defines visual styling and layout rules for a web page or application.",
        "",
        "Language",
        "CSS",
        "",
        "Detected Selectors",
        selectors_text,
        "",
        "Styling Features",
        features_text,
        "",
        "Developer Notes",
        "- This file controls the appearance of HTML elements.",
        "- It may include layout, spacing, colors, typography, and responsive design rules.",
    ]

    return "\n".join(doc_lines)


def analyze_sql(code):
    lower_code = code.lower()

    operations = []

    if "select " in lower_code:
        operations.append("- Reads data from a database table using SELECT.")

    if "insert into" in lower_code:
        operations.append("- Inserts new records into a database table.")

    if "update " in lower_code:
        operations.append("- Updates existing records in a database table.")

    if "delete from" in lower_code:
        operations.append("- Deletes records from a database table.")

    if "create table" in lower_code:
        operations.append("- Creates a new database table.")

    if "drop table" in lower_code:
        operations.append("- Deletes/removes a database table.")

    if "join " in lower_code:
        operations.append("- Combines data from multiple tables using JOIN.")

    if "where " in lower_code:
        operations.append("- Filters records using a WHERE condition.")

    if "group by" in lower_code:
        operations.append("- Groups records using GROUP BY.")

    if "order by" in lower_code:
        operations.append("- Sorts records using ORDER BY.")

    if operations:
        operations_text = "\n".join(operations)
    else:
        operations_text = "- No common SQL operations detected."

    tables = []
    words = code.replace(";", " ").replace(",", " ").split()

    for i, word in enumerate(words):
        word_lower = word.lower()

        if word_lower in ["from", "into", "update", "table", "join"]:
            if i + 1 < len(words):
                table_name = words[i + 1].strip()
                if table_name not in tables:
                    tables.append(table_name)

    if tables:
        tables_text = "\n".join(
            ["- `" + table + "` table is referenced." for table in tables]
        )
    else:
        tables_text = "- No table names clearly detected."

    doc_lines = [
        "SQL Documentation",
        "",
        "Description",
        "This SQL code is used to interact with a database by reading, creating, updating, or managing data.",
        "",
        "Language",
        "SQL",
        "",
        "Detected Tables",
        tables_text,
        "",
        "Detected Operations",
        operations_text,
        "",
        "Developer Notes",
        "- Review WHERE conditions carefully before running UPDATE or DELETE queries.",
        "- Make sure table and column names match the database schema.",
        "- Test queries on sample data before using them in production.",
    ]

    return "\n".join(doc_lines)


def analyze_php(code):
    lower_code = code.lower()

    features = []

    if "<?php" in lower_code:
        features.append("- Contains PHP opening tag.")

    if "echo " in lower_code:
        features.append("- Outputs content using echo.")

    if "function " in lower_code:
        features.append("- Defines one or more PHP functions.")

    if "$" in code:
        features.append("- Uses PHP variables.")

    if "if " in lower_code:
        features.append("- Contains conditional logic.")

    if "foreach" in lower_code or "for " in lower_code or "while " in lower_code:
        features.append("- Contains loop logic.")

    if "mysqli" in lower_code or "pdo" in lower_code:
        features.append("- May connect to or interact with a database.")

    if features:
        features_text = "\n".join(features)
    else:
        features_text = "- No common PHP features detected."

    functions = []
    lines = code.splitlines()

    for line in lines:
        clean_line = line.strip()

        if clean_line.startswith("function "):
            function_name = clean_line.replace("function ", "").split("(")[0].strip()
            functions.append(function_name)

    if functions:
        functions_text = "\n".join(
            ["- `" + fn + "` function is defined." for fn in functions]
        )
    else:
        functions_text = "- No PHP functions clearly detected."

    doc_lines = [
        "PHP File Documentation",
        "",
        "File Description",
        "This PHP file contains server-side logic that may process data, generate dynamic output, or interact with a database.",
        "",
        "Language",
        "PHP",
        "",
        "Detected Functions",
        functions_text,
        "",
        "Detected Features",
        features_text,
        "",
        "Developer Notes",
        "- PHP code runs on the server before the response is sent to the browser.",
        "- Check input validation and security when handling user data.",
        "- Database queries should be protected against SQL injection.",
    ]

    return "\n".join(doc_lines)


def analyze_java(code):
    lower_code = code.lower()

    features = []

    if "public class" in code:
        features.append("- Defines a public Java class.")

    if "public static void main" in lower_code:
        features.append("- Contains the main method used to start the Java program.")

    if "system.out.println" in lower_code:
        features.append("- Prints output to the console.")

    if "if " in lower_code:
        features.append("- Contains conditional logic.")

    if "for " in lower_code or "while " in lower_code:
        features.append("- Contains loop logic.")

    if "return " in lower_code:
        features.append("- Returns a value from a method.")

    if features:
        features_text = "\n".join(features)
    else:
        features_text = "- No common Java features detected."

    class_name = "UnknownClass"
    methods = []

    lines = code.splitlines()

    for line in lines:
        clean_line = line.strip()

        if "class " in clean_line:
            class_name = clean_line.split("class ")[1].split("{")[0].strip()

        if "(" in clean_line and ")" in clean_line and "{" in clean_line:
            if (
                "class " not in clean_line
                and not clean_line.startswith("if")
                and not clean_line.startswith("for")
                and not clean_line.startswith("while")
            ):
                method_name = clean_line.split("(")[0].split()[-1]
                methods.append(method_name)

    if methods:
        methods_text = "\n".join(
            ["- `" + method + "` method is defined." for method in methods]
        )
    else:
        methods_text = "- No Java methods clearly detected."

    doc_lines = [
        class_name + " Java File Documentation",
        "",
        "File Description",
        "This Java file defines a class and may contain methods used to perform application logic.",
        "",
        "Language",
        "Java",
        "",
        "Detected Class",
        "- `" + class_name + "` class is defined.",
        "",
        "Detected Methods",
        methods_text,
        "",
        "Detected Features",
        features_text,
        "",
        "Developer Notes",
        "- Java code is usually organized into classes and methods.",
        "- The main method is the starting point for a standalone Java program.",
        "- Check method visibility such as public, private, or protected when reviewing the code.",
    ]

    return "\n".join(doc_lines)


def analyze_c_cpp(code):
    lower_code = code.lower()

    features = []

    if "#include" in code:
        features.append("- Includes external libraries or header files.")

    if "int main" in lower_code:
        features.append(
            "- Contains the main function, which is the program entry point."
        )

    if "printf" in lower_code:
        features.append("- Uses printf to display output.")

    if "cout" in lower_code:
        features.append("- Uses cout to display output.")

    if "scanf" in lower_code or "cin" in lower_code:
        features.append("- Takes input from the user.")

    if "if " in lower_code:
        features.append("- Contains conditional logic.")

    if "for " in lower_code or "while " in lower_code:
        features.append("- Contains loop logic.")

    if "return " in lower_code:
        features.append("- Returns a value from a function.")

    if features:
        features_text = "\n".join(features)
    else:
        features_text = "- No common C/C++ features detected."

    functions = []
    lines = code.splitlines()

    for line in lines:
        clean_line = line.strip()

        if "(" in clean_line and ")" in clean_line and "{" in clean_line:
            if (
                not clean_line.startswith("if")
                and not clean_line.startswith("for")
                and not clean_line.startswith("while")
            ):
                before_parenthesis = clean_line.split("(")[0].strip()
                parts = before_parenthesis.split()

                if len(parts) >= 2:
                    function_name = parts[-1]
                    if function_name not in functions:
                        functions.append(function_name)

    if functions:
        functions_text = "\n".join(
            ["- `" + fn + "` function is defined." for fn in functions]
        )
    else:
        functions_text = "- No C/C++ functions clearly detected."

    if "cout" in lower_code or "iostream" in lower_code:
        language_name = "C++"
    else:
        language_name = "C/C++"

    doc_lines = [
        language_name + " File Documentation",
        "",
        "File Description",
        "This file contains C or C++ code that defines functions and program logic.",
        "",
        "Language",
        language_name,
        "",
        "Detected Functions",
        functions_text,
        "",
        "Detected Features",
        features_text,
        "",
        "Developer Notes",
        "- C and C++ programs usually start from the main function.",
        "- Header files are included using #include.",
        "- Review memory handling carefully when working with pointers or dynamic allocation.",
    ]

    return "\n".join(doc_lines)


def analyze_unknown(code):
    lines = code.splitlines()
    line_count = len(lines)

    non_empty_lines = [line for line in lines if line.strip()]
    non_empty_count = len(non_empty_lines)

    doc_lines = [
        "Code Analysis",
        "",
        "File Description",
        "The system could not confidently detect the programming language, but it can still provide a basic structure summary.",
        "",
        "Language",
        "Unknown",
        "",
        "Code Size",
        "- Total lines: " + str(line_count),
        "- Non-empty lines: " + str(non_empty_count),
        "",
        "Basic Observations",
    ]

    if "{" in code and "}" in code:
        doc_lines.append(
            "- The code uses curly braces, which are common in languages like JavaScript, Java, C, C++, PHP, or C#."
        )

    if ";" in code:
        doc_lines.append(
            "- The code uses semicolons, which are common in many programming languages."
        )

    if "<" in code and ">" in code:
        doc_lines.append(
            "- The code contains angle brackets, which may indicate markup or generic syntax."
        )

    if "=" in code:
        doc_lines.append("- The code appears to contain assignments or comparisons.")

    if len(doc_lines) == 12:
        doc_lines.append("- No clear programming structure detected.")

    doc_lines.extend(
        [
            "",
            "Developer Notes",
            "- Please provide a larger code sample or full file for more accurate analysis.",
            "- Future versions can support more languages and project-level scanning.",
        ]
    )

    return "\n".join(doc_lines)


def analyze_react(code):
    lower_code = code.lower()
    lines = code.splitlines()

    components = []
    hooks = []
    features = []

    for line in lines:
        clean_line = line.strip()

        if clean_line.startswith("function "):
            name = clean_line.replace("function ", "").split("(")[0].strip()
            if name and name[0].isupper():
                components.append(name)

        if "const " in clean_line and "= (" in clean_line and "=>" in clean_line:
            name = clean_line.split("const ")[1].split("=")[0].strip()
            if name and name[0].isupper():
                components.append(name)

        if "useState" in clean_line:
            hooks.append("useState")
            features.append("- Uses state management with useState.")

        if "useEffect" in clean_line:
            hooks.append("useEffect")
            features.append("- Uses useEffect for side effects or lifecycle behavior.")

        if "props" in clean_line:
            features.append("- Uses props to receive data from a parent component.")

        if "onClick" in clean_line:
            features.append("- Handles click events.")

        if "className" in clean_line:
            features.append("- Uses className for CSS styling.")

        if "return (" in clean_line:
            features.append("- Returns JSX markup for rendering UI.")

    if components:
        components_text = "\n".join(
            [
                "- `" + component + "` component is defined."
                for component in sorted(set(components))
            ]
        )
    else:
        components_text = "- No React component clearly detected."

    if hooks:
        hooks_text = "\n".join(
            ["- `" + hook + "` hook is used." for hook in sorted(set(hooks))]
        )
    else:
        hooks_text = "- No React hooks detected."

    if features:
        features_text = "\n".join(sorted(set(features)))
    else:
        features_text = "- No common React features detected."

    doc_lines = [
        "React / JSX File Documentation",
        "",
        "File Description",
        "This file contains a React component or JSX-based UI logic used to build part of a frontend application.",
        "",
        "Detected Technology",
        "React.js",
        "",
        "Language",
        "JavaScript / JSX",
        "",
        "Detected Components",
        components_text,
        "",
        "Detected Hooks",
        hooks_text,
        "",
        "Detected Features",
        features_text,
        "",
        "Developer Notes",
        "- React components return JSX that describes the user interface.",
        "- State and effects are usually handled with React hooks.",
        "- Styling may be connected through CSS files, CSS modules, or utility classes.",
    ]

    return "\n".join(doc_lines)


def analyze_react_native(code):
    lower_code = code.lower()
    lines = code.splitlines()

    components = []
    native_components = []
    hooks = []
    features = []

    common_native_components = [
        "View",
        "Text",
        "Image",
        "Button",
        "TextInput",
        "TouchableOpacity",
        "FlatList",
        "ScrollView",
        "SafeAreaView",
    ]

    for component in common_native_components:
        if component in code:
            native_components.append(component)

    for line in lines:
        clean_line = line.strip()

        if clean_line.startswith("function "):
            name = clean_line.replace("function ", "").split("(")[0].strip()
            if name and name[0].isupper():
                components.append(name)

        if "const " in clean_line and "= (" in clean_line and "=>" in clean_line:
            name = clean_line.split("const ")[1].split("=")[0].strip()
            if name and name[0].isupper():
                components.append(name)

        if "useState" in clean_line:
            hooks.append("useState")
            features.append("- Uses useState to manage mobile screen state.")

        if "useEffect" in clean_line:
            hooks.append("useEffect")
            features.append("- Uses useEffect for lifecycle behavior or side effects.")

        if "onPress" in clean_line:
            features.append("- Handles mobile press/tap events.")

        if "StyleSheet.create" in clean_line:
            features.append("- Uses StyleSheet.create for React Native styling.")

        if "navigation" in clean_line:
            features.append("- May use navigation for moving between screens.")

    if components:
        components_text = "\n".join(
            [
                "- `" + component + "` screen/component is defined."
                for component in sorted(set(components))
            ]
        )
    else:
        components_text = "- No React Native component clearly detected."

    if native_components:
        native_components_text = "\n".join(
            [
                "- `" + component + "` native component is used."
                for component in sorted(set(native_components))
            ]
        )
    else:
        native_components_text = "- No common React Native UI components detected."

    if hooks:
        hooks_text = "\n".join(
            ["- `" + hook + "` hook is used." for hook in sorted(set(hooks))]
        )
    else:
        hooks_text = "- No React hooks detected."

    if features:
        features_text = "\n".join(sorted(set(features)))
    else:
        features_text = "- No common React Native features detected."

    doc_lines = [
        "React Native File Documentation",
        "",
        "File Description",
        "This file contains React Native code used to build mobile application screens or components.",
        "",
        "Detected Technology",
        "React Native",
        "",
        "Language",
        "JavaScript / JSX",
        "",
        "Detected Screens or Components",
        components_text,
        "",
        "Detected Native UI Components",
        native_components_text,
        "",
        "Detected Hooks",
        hooks_text,
        "",
        "Detected Features",
        features_text,
        "",
        "Developer Notes",
        "- React Native uses JavaScript and JSX to build mobile user interfaces.",
        "- Components such as View, Text, and TouchableOpacity are used instead of HTML elements.",
        "- Styling is commonly handled using StyleSheet.create.",
    ]

    return "\n".join(doc_lines)


def analyze_flutter(code):
    lower_code = code.lower()
    lines = code.splitlines()

    classes = []
    widgets = []
    features = []

    common_widgets = [
        "Scaffold",
        "AppBar",
        "Text",
        "Container",
        "Column",
        "Row",
        "ListView",
        "GridView",
        "TextField",
        "ElevatedButton",
        "IconButton",
        "Image",
        "Center",
        "Padding",
        "MaterialApp",
    ]

    for widget in common_widgets:
        if widget in code:
            widgets.append(widget)

    for line in lines:
        clean_line = line.strip()

        if clean_line.startswith("class "):
            class_name = clean_line.replace("class ", "").split(" ")[0].strip()
            classes.append(class_name)

            if "StatelessWidget" in clean_line:
                features.append("- Defines a stateless Flutter widget.")

            if "StatefulWidget" in clean_line:
                features.append("- Defines a stateful Flutter widget.")

        if "Widget build" in clean_line:
            features.append("- Contains a build method that returns the widget UI.")

        if "setState" in clean_line:
            features.append("- Uses setState to update UI state.")

        if "Navigator" in clean_line:
            features.append("- Uses Navigator for screen navigation.")

        if "onPressed" in clean_line:
            features.append("- Handles button press events.")

    if classes:
        classes_text = "\n".join(
            ["- `" + cls + "` class is defined." for cls in sorted(set(classes))]
        )
    else:
        classes_text = "- No Flutter/Dart class clearly detected."

    if widgets:
        widgets_text = "\n".join(
            ["- `" + widget + "` widget is used." for widget in sorted(set(widgets))]
        )
    else:
        widgets_text = "- No common Flutter widgets detected."

    if features:
        features_text = "\n".join(sorted(set(features)))
    else:
        features_text = "- No common Flutter features detected."

    doc_lines = [
        "Flutter / Dart File Documentation",
        "",
        "File Description",
        "This file contains Flutter code written in Dart and is used to build mobile or cross-platform user interfaces.",
        "",
        "Detected Technology",
        "Flutter",
        "",
        "Language",
        "Dart",
        "",
        "Detected Classes",
        classes_text,
        "",
        "Detected Widgets",
        widgets_text,
        "",
        "Detected Features",
        features_text,
        "",
        "Developer Notes",
        "- Flutter uses widgets to build user interfaces.",
        "- StatelessWidget is used for static UI, while StatefulWidget is used for UI that changes over time.",
        "- The build method describes what appears on the screen.",
    ]

    return "\n".join(doc_lines)


def analyze_kotlin(code):
    lower_code = code.lower()
    lines = code.splitlines()

    classes = []
    functions = []
    features = []

    if "fun main" in lower_code:
        features.append(
            "- Contains a main function, which can be the program entry point."
        )

    if "println(" in lower_code:
        features.append("- Prints output to the console.")

    if "val " in lower_code:
        features.append("- Uses immutable variables declared with val.")

    if "var " in lower_code:
        features.append("- Uses mutable variables declared with var.")

    if "if " in lower_code:
        features.append("- Contains conditional logic.")

    if "for " in lower_code or "while " in lower_code:
        features.append("- Contains loop logic.")

    if "return " in lower_code:
        features.append("- Returns a value from a function.")

    if "activity" in lower_code:
        features.append("- May be related to Android Activity logic.")

    for line in lines:
        clean_line = line.strip()

        if clean_line.startswith("class "):
            class_name = (
                clean_line.replace("class ", "").split("(")[0].split(":")[0].strip()
            )
            classes.append(class_name)

        if clean_line.startswith("fun "):
            function_name = clean_line.replace("fun ", "").split("(")[0].strip()
            functions.append(function_name)

    if classes:
        classes_text = "\n".join(
            ["- `" + cls + "` class is defined." for cls in sorted(set(classes))]
        )
    else:
        classes_text = "- No Kotlin class clearly detected."

    if functions:
        functions_text = "\n".join(
            ["- `" + fn + "` function is defined." for fn in sorted(set(functions))]
        )
    else:
        functions_text = "- No Kotlin functions clearly detected."

    if features:
        features_text = "\n".join(sorted(set(features)))
    else:
        features_text = "- No common Kotlin features detected."

    doc_lines = [
        "Kotlin File Documentation",
        "",
        "File Description",
        "This file contains Kotlin code that may define application logic, Android components, or general program behavior.",
        "",
        "Language",
        "Kotlin",
        "",
        "Detected Classes",
        classes_text,
        "",
        "Detected Functions",
        functions_text,
        "",
        "Detected Features",
        features_text,
        "",
        "Developer Notes",
        "- Kotlin is commonly used for Android development and JVM applications.",
        "- val creates immutable variables, while var creates mutable variables.",
        "- Kotlin functions are declared using the fun keyword.",
    ]

    return "\n".join(doc_lines)


def analyze_swift(code):
    lower_code = code.lower()
    lines = code.splitlines()

    classes = []
    structs = []
    functions = []
    features = []

    if "import swiftui" in lower_code:
        features.append("- Uses SwiftUI for building user interfaces.")

    if "import uikit" in lower_code:
        features.append("- Uses UIKit for iOS user interface development.")

    if "print(" in lower_code:
        features.append("- Prints output to the console.")

    if "let " in lower_code:
        features.append("- Uses constants declared with let.")

    if "var " in lower_code:
        features.append("- Uses variables declared with var.")

    if "if " in lower_code:
        features.append("- Contains conditional logic.")

    if "for " in lower_code or "while " in lower_code:
        features.append("- Contains loop logic.")

    if "return " in lower_code:
        features.append("- Returns a value from a function.")

    if "view" in lower_code:
        features.append("- May define or manage a user interface view.")

    for line in lines:
        clean_line = line.strip()

        if clean_line.startswith("class "):
            class_name = (
                clean_line.replace("class ", "").split(":")[0].split("{")[0].strip()
            )
            classes.append(class_name)

        if clean_line.startswith("struct "):
            struct_name = (
                clean_line.replace("struct ", "").split(":")[0].split("{")[0].strip()
            )
            structs.append(struct_name)

        if clean_line.startswith("func "):
            function_name = clean_line.replace("func ", "").split("(")[0].strip()
            functions.append(function_name)

    if classes:
        classes_text = "\n".join(
            ["- `" + cls + "` class is defined." for cls in sorted(set(classes))]
        )
    else:
        classes_text = "- No Swift class clearly detected."

    if structs:
        structs_text = "\n".join(
            ["- `" + struct + "` struct is defined." for struct in sorted(set(structs))]
        )
    else:
        structs_text = "- No Swift struct clearly detected."

    if functions:
        functions_text = "\n".join(
            ["- `" + fn + "` function is defined." for fn in sorted(set(functions))]
        )
    else:
        functions_text = "- No Swift functions clearly detected."

    if features:
        features_text = "\n".join(sorted(set(features)))
    else:
        features_text = "- No common Swift features detected."

    doc_lines = [
        "Swift File Documentation",
        "",
        "File Description",
        "This file contains Swift code that may define iOS/macOS application logic, UI views, classes, structs, or functions.",
        "",
        "Language",
        "Swift",
        "",
        "Detected Classes",
        classes_text,
        "",
        "Detected Structs",
        structs_text,
        "",
        "Detected Functions",
        functions_text,
        "",
        "Detected Features",
        features_text,
        "",
        "Developer Notes",
        "- Swift is commonly used for iOS, macOS, watchOS, and tvOS development.",
        "- SwiftUI uses structs and View composition to build user interfaces.",
        "- let declares constants, while var declares mutable variables.",
    ]

    return "\n".join(doc_lines)


def analyze_typescript(code):
    lower_code = code.lower()
    lines = code.splitlines()

    interfaces = []
    types = []
    functions = []
    features = []

    if "interface " in lower_code:
        features.append("- Defines one or more interfaces for object structure.")

    if "type " in lower_code:
        features.append("- Defines one or more custom type aliases.")

    if ": string" in lower_code:
        features.append("- Uses string type annotations.")

    if ": number" in lower_code:
        features.append("- Uses number type annotations.")

    if ": boolean" in lower_code:
        features.append("- Uses boolean type annotations.")

    if "return " in lower_code:
        features.append("- Returns a value from a function.")

    if "async " in lower_code or "await " in lower_code:
        features.append("- Uses asynchronous logic with async/await.")

    if "console.log" in lower_code:
        features.append("- Prints output to the console.")

    for line in lines:
        clean_line = line.strip()

        if clean_line.startswith("interface "):
            interface_name = clean_line.replace("interface ", "").split("{")[0].strip()
            interfaces.append(interface_name)

        if clean_line.startswith("type "):
            type_name = clean_line.replace("type ", "").split("=")[0].strip()
            types.append(type_name)

        if clean_line.startswith("function "):
            function_name = clean_line.replace("function ", "").split("(")[0].strip()
            functions.append(function_name)

        if clean_line.startswith("const ") and "=>" in clean_line:
            function_name = clean_line.replace("const ", "").split("=")[0].strip()
            functions.append(function_name)

    if interfaces:
        interfaces_text = "\n".join(
            [
                "- `" + interface + "` interface is defined."
                for interface in sorted(set(interfaces))
            ]
        )
    else:
        interfaces_text = "- No TypeScript interfaces clearly detected."

    if types:
        types_text = "\n".join(
            [
                "- `" + type_name + "` type alias is defined."
                for type_name in sorted(set(types))
            ]
        )
    else:
        types_text = "- No TypeScript type aliases clearly detected."

    if functions:
        functions_text = "\n".join(
            ["- `" + fn + "` function is defined." for fn in sorted(set(functions))]
        )
    else:
        functions_text = "- No TypeScript functions clearly detected."

    if features:
        features_text = "\n".join(sorted(set(features)))
    else:
        features_text = "- No common TypeScript features detected."

    doc_lines = [
        "TypeScript File Documentation",
        "",
        "File Description",
        "This file contains TypeScript code, which adds static typing to JavaScript for safer and more maintainable applications.",
        "",
        "Language",
        "TypeScript",
        "",
        "Detected Interfaces",
        interfaces_text,
        "",
        "Detected Type Aliases",
        types_text,
        "",
        "Detected Functions",
        functions_text,
        "",
        "Detected Features",
        features_text,
        "",
        "Developer Notes",
        "- TypeScript helps catch errors before runtime using type annotations.",
        "- Interfaces and types describe the shape of data used in the application.",
        "- TypeScript is commonly used with React, Node.js, and enterprise web applications.",
    ]

    return "\n".join(doc_lines)


def analyze_csharp(code):
    lower_code = code.lower()
    lines = code.splitlines()

    namespaces = []
    classes = []
    methods = []
    features = []

    if "using system" in lower_code:
        features.append("- Uses the System namespace for common .NET functionality.")

    if "namespace " in lower_code:
        features.append("- Defines a namespace to organize code.")

    if "public class" in lower_code or "class " in lower_code:
        features.append("- Defines one or more C# classes.")

    if "static void main" in lower_code:
        features.append(
            "- Contains the Main method, which can be the application entry point."
        )

    if "console.writeline" in lower_code:
        features.append("- Prints output to the console.")

    if "if " in lower_code:
        features.append("- Contains conditional logic.")

    if "for " in lower_code or "while " in lower_code or "foreach" in lower_code:
        features.append("- Contains loop logic.")

    if "return " in lower_code:
        features.append("- Returns a value from a method.")

    for line in lines:
        clean_line = line.strip()

        if clean_line.startswith("namespace "):
            namespace_name = clean_line.replace("namespace ", "").split("{")[0].strip()
            namespaces.append(namespace_name)

        if "class " in clean_line:
            class_name = (
                clean_line.split("class ")[1].split("{")[0].split(":")[0].strip()
            )
            classes.append(class_name)

        if "(" in clean_line and ")" in clean_line and "{" in clean_line:
            if (
                "class " not in clean_line
                and not clean_line.startswith("if")
                and not clean_line.startswith("for")
                and not clean_line.startswith("while")
                and not clean_line.startswith("foreach")
            ):
                method_name = clean_line.split("(")[0].split()[-1]
                methods.append(method_name)

    if namespaces:
        namespaces_text = "\n".join(
            ["- `" + ns + "` namespace is defined." for ns in sorted(set(namespaces))]
        )
    else:
        namespaces_text = "- No C# namespace clearly detected."

    if classes:
        classes_text = "\n".join(
            ["- `" + cls + "` class is defined." for cls in sorted(set(classes))]
        )
    else:
        classes_text = "- No C# class clearly detected."

    if methods:
        methods_text = "\n".join(
            ["- `" + method + "` method is defined." for method in sorted(set(methods))]
        )
    else:
        methods_text = "- No C# methods clearly detected."

    if features:
        features_text = "\n".join(sorted(set(features)))
    else:
        features_text = "- No common C# features detected."

    doc_lines = [
        "C# File Documentation",
        "",
        "File Description",
        "This file contains C# code used for .NET applications, backend services, desktop software, or game development.",
        "",
        "Language",
        "C#",
        "",
        "Detected Namespaces",
        namespaces_text,
        "",
        "Detected Classes",
        classes_text,
        "",
        "Detected Methods",
        methods_text,
        "",
        "Detected Features",
        features_text,
        "",
        "Developer Notes",
        "- C# code is commonly organized into namespaces, classes, and methods.",
        "- The Main method can act as the entry point for a console application.",
        "- C# is widely used in ASP.NET, desktop applications, and Unity development.",
    ]

    return "\n".join(doc_lines)


def split_multiple_files(code):
    files = []

    pattern = r"^--- FILE: (.*?) ---$"
    matches = list(re.finditer(pattern, code, re.MULTILINE))

    if not matches:
        return files

    for index, match in enumerate(matches):
        file_name = match.group(1).strip()
        content_start = match.end()

        if index + 1 < len(matches):
            content_end = matches[index + 1].start()
        else:
            content_end = len(code)

        file_content = code[content_start:content_end].strip()

        files.append({"file_name": file_name, "content": file_content})

    return files


def get_file_metrics(code):
    lines = code.splitlines()
    non_empty_lines = [line for line in lines if line.strip()]

    return {"total_lines": len(lines), "non_empty_lines": len(non_empty_lines)}


def analyze_single_file(code, file_name=""):
    language = detect_language(code, file_name)

    if language == "React Native":
        doc = analyze_react_native(code)
        return doc, language

    if language == "React":
        doc = analyze_react(code)
        return doc, language

    if language == "React / TypeScript":
        doc = analyze_react(code)
        return doc, language

    if language == "TypeScript":
        doc = analyze_typescript(code)
        return doc, language

    if language == "JavaScript":
        doc = analyze_javascript(code)
        return doc, language

    if language == "HTML":
        doc = analyze_html(code)
        return doc, language

    if language == "CSS":
        doc = analyze_css(code)
        return doc, language

    if language == "SQL":
        doc = analyze_sql(code)
        return doc, language

    if language == "PHP":
        doc = analyze_php(code)
        return doc, language

    if language == "Flutter":
        doc = analyze_flutter(code)
        return doc, language

    if language == "Swift":
        doc = analyze_swift(code)
        return doc, language

    if language == "Kotlin":
        doc = analyze_kotlin(code)
        return doc, language

    if language == "C#":
        doc = analyze_csharp(code)
        return doc, language

    if language == "Java":
        doc = analyze_java(code)
        return doc, language

    if language == "C/C++":
        doc = analyze_c_cpp(code)
        return doc, language

    if language == "Unknown":
        doc = analyze_unknown(code)
        return doc, language

    # Python fallback
    try:
        tree = ast.parse(code)
        docs = []

        function_names = []

        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                function_names.append(node.name)

        if len(function_names) > 8:
            important_functions = function_names[:8]
            function_categories = categorize_python_functions(function_names)
            summary = [
                "Large Python File Summary",
                "",
                "File Description",
                "This Python file contains multiple functions and appears to be a larger application or backend file.",
                "",
                "Language",
                "Python",
                "",
                "Total Functions",
                "- " + str(len(function_names)),
                "",
                "Important Functions",
                "\n".join(["- `" + name + "`" for name in important_functions]),
                "",
                "Function Categories",
                function_categories,
                "",
                "Developer Notes",
                "- This file is large, so the system generated a summary instead of full documentation for every function.",
                "- For detailed documentation, upload a smaller file or a specific function."
            ]

            return "\n".join(summary), language

        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                docs.append(generate_function_doc(node))

        if docs:
            return "\n\n".join(docs), language

        return "No Python function found in this file.", language

    except Exception:
        doc = analyze_unknown(code)
        return doc, "Unknown"


def generate_folder_structure(uploaded_files):
    file_names = [file["file_name"] for file in uploaded_files]

    structure_lines = []

    for name in file_names:
        parts = name.split("/")
        indent = ""

        for index, part in enumerate(parts):
            line = indent + "- " + part

            if line not in structure_lines:
                structure_lines.append(line)

            indent += "  "

    if structure_lines:
        return "\n".join(structure_lines)

    return "- No folder structure detected."


def detect_project_type(uploaded_files, languages):
    file_names = [file["file_name"].lower() for file in uploaded_files]
    language_set = set(languages)

    has_package_json = any(name.endswith("package.json") for name in file_names)

    has_real_frontend = any(
        name.endswith("index.html")
        or name.endswith("app.jsx")
        or name.endswith("app.tsx")
        or name.endswith("src/app.js")
        or name.endswith("src/index.js")
        or name.endswith("src/main.js")
        or name.endswith("src/app.jsx")
        or name.endswith("src/app.tsx")
        for name in file_names
    )

    has_react_import = any(
        (
            "import react" in file["content"].lower()
            or "from 'react'" in file["content"].lower()
            or 'from "react"' in file["content"].lower()
        )
        for file in uploaded_files
        if file["file_name"].lower().endswith((".js", ".jsx", ".ts", ".tsx"))
    )

    has_frontend = (
        has_package_json
        or has_real_frontend
        or has_react_import
        or "React" in language_set
        or "React Native" in language_set
        or "HTML" in language_set
        or "CSS" in language_set
    )

    has_flask = any(
        "from flask import" in file["content"].lower()
        or "flask(__name__)" in file["content"].lower()
        for file in uploaded_files
    )

    has_django = any(
        name.endswith("settings.py")
        or name.endswith("urls.py")
        or name.endswith("manage.py")
        for name in file_names
    )

    has_backend = (
        has_flask
        or has_django
        or "Python" in language_set
        or "PHP" in language_set
        or "Java" in language_set
        or "C#" in language_set
        or "Kotlin" in language_set
    )

    has_database = "SQL" in language_set

    if has_django and has_frontend:
        return "Full Stack Django Web Application"

    if has_flask and has_frontend:
        return "Full Stack Flask Web Application"

    if has_django:
        return "Django Web Application"

    if has_flask:
        return "Flask Backend Application"

    if has_frontend and has_backend:
        return "Full Stack Project"

    if has_frontend:
        return "Frontend Project"

    if has_backend:
        return "Backend Project"

    if has_database:
        return "Database/SQL Project"

    return "General Code Project"


def categorize_python_functions(function_names):
    categories = []

    language_functions = []
    analysis_functions = []
    documentation_functions = []
    utility_functions = []

    for name in function_names:
        if "detect" in name or "language" in name:
            language_functions.append(name)
        elif "analyze" in name:
            analysis_functions.append(name)
        elif "doc" in name or "documentation" in name or "summary" in name:
            documentation_functions.append(name)
        else:
            utility_functions.append(name)

    if language_functions:
        categories.append(
            "Language Detection Functions\n"
            + "\n".join(["- `" + name + "`" for name in language_functions[:8]])
        )

    if analysis_functions:
        categories.append(
            "Code Analysis Functions\n"
            + "\n".join(["- `" + name + "`" for name in analysis_functions[:8]])
        )

    if documentation_functions:
        categories.append(
            "Documentation Generation Functions\n"
            + "\n".join(["- `" + name + "`" for name in documentation_functions[:8]])
        )

    if utility_functions:
        categories.append(
            "Utility / Helper Functions\n"
            + "\n".join(["- `" + name + "`" for name in utility_functions[:8]])
        )

    if categories:
        return "\n\n".join(categories)

    return "- No function categories detected."


def get_project_metrics(uploaded_files):
    total_lines = 0
    total_non_empty_lines = 0

    for file in uploaded_files:
        metrics = get_file_metrics(file["content"])
        total_lines += metrics["total_lines"]
        total_non_empty_lines += metrics["non_empty_lines"]

    return {"total_lines": total_lines, "non_empty_lines": total_non_empty_lines}


def detect_flask_routes(code):
    routes = []
    lines = code.splitlines()

    for index, line in enumerate(lines):
        clean_line = line.strip()

        if clean_line.startswith("@app.route"):
            route_path = "Unknown route"
            methods = "GET"

            route_match = re.search(r'@app\.route\(["\'](.*?)["\']', clean_line)
            if route_match:
                route_path = route_match.group(1)

            methods_match = re.search(r"methods=\[(.*?)\]", clean_line)
            if methods_match:
                methods = methods_match.group(1).replace('"', "").replace("'", "")

            function_name = "Unknown handler"

            if index + 1 < len(lines):
                next_line = lines[index + 1].strip()
                if next_line.startswith("def "):
                    function_name = next_line.replace("def ", "").split("(")[0].strip()

            purpose = "Handles requests for this route."

            if "generate" in function_name:
                purpose = "Handles documentation generation requests."
            elif "upload" in function_name:
                purpose = "Handles file upload requests."
            elif "login" in function_name:
                purpose = "Handles user login requests."
            elif "register" in function_name:
                purpose = "Handles user registration requests."

            request_info = "Request body not clearly detected."
            response_info = "Response format not clearly detected."

            if "request.json" in code or "request.get_json" in code:
                request_info = "Expects JSON request data."

            if "request.files" in code:
                request_info = "Expects uploaded file data."

            if "jsonify" in code:
                response_info = "Returns a JSON response."

            route_doc = (
                "- Route: `"
                + route_path
                + "`\n"
                + "  Methods: `"
                + methods
                + "`\n"
                + "  Handler: `"
                + function_name
                + "`\n"
                + "  Request: "
                + request_info
                + "\n"
                + "  Response: "
                + response_info
                + "\n"
                + "  Purpose: "
                + purpose
            )

            routes.append(route_doc)

    if routes:
        return "\n".join(routes)

    return "- No Flask routes detected."


def generate_api_endpoint_summary(uploaded_files):
    endpoints = []

    for file in uploaded_files:
        if file["file_name"].lower().endswith(".py"):
            routes = detect_flask_routes(file["content"])

            if routes != "- No Flask routes detected.":
                endpoints.append("File: `" + file["file_name"] + "`")
                endpoints.append(routes)

    if endpoints:
        return "\n".join(endpoints)

    return "- No API endpoints detected."

def generate_backend_workflow_summary(uploaded_files):
    workflow = []

    has_flask = False
    has_json_request = False
    has_json_response = False
    has_analysis = False
    has_routes = False

    for file in uploaded_files:
        name = file["file_name"].lower()
        content = file["content"].lower()

        if name.endswith(".py"):
            if "from flask import" in content or "flask(__name__)" in content:
                has_flask = True

            if "@app.route" in content:
                has_routes = True

            if "request.json" in content or "request.get_json" in content:
                has_json_request = True

            if "jsonify" in content:
                has_json_response = True

            if "analyze_" in content or "detect_" in content:
                has_analysis = True

    if has_flask:
        workflow.append("- Starts a Flask backend application.")

    if has_routes:
        workflow.append("- Defines API routes for frontend communication.")

    if has_json_request:
        workflow.append("- Receives JSON data from the frontend.")

    if has_analysis:
        workflow.append("- Analyzes uploaded code, files, languages, routes, and project structure.")

    if has_json_response:
        workflow.append("- Returns generated documentation as a JSON response.")

    if workflow:
        return "\n".join(workflow)

    return "- Backend workflow could not be clearly detected."


def generate_frontend_workflow_summary(uploaded_files):
    workflow = []

    has_js = False
    has_react = False
    has_upload = False
    has_fetch = False
    has_buttons = False

    for file in uploaded_files:
        name = file["file_name"].lower()
        content = file["content"].lower()

        if name.endswith((".js", ".jsx", ".ts", ".tsx", ".html", ".css")):
            has_js = True

        if "import react" in content or "usestate" in content or "jsx" in content:
            has_react = True

        if 'type="file"' in content or "filereader" in content:
            has_upload = True

        if "fetch(" in content or "axios." in content:
            has_fetch = True

        if "button" in content or "onclick" in content:
            has_buttons = True

    if has_react:
        workflow.append("- Uses React or JSX-based UI logic.")

    if has_js:
        workflow.append("- Contains frontend or client-side code.")

    if has_upload:
        workflow.append("- Allows users to upload files or project folders.")

    if has_fetch:
        workflow.append("- Sends data from the frontend to the backend API.")

    if has_buttons:
        workflow.append(
            "- Provides user actions such as generate, copy, export, or clear."
        )

    if workflow:
        return "\n".join(workflow)

    return "- Frontend workflow could not be clearly detected."


def generate_architecture_summary(uploaded_files):
    has_frontend = False
    has_backend = False
    has_api = False
    has_json = False

    for file in uploaded_files:
        name = file["file_name"].lower()
        content = file["content"].lower()

        if name.endswith((".js", ".jsx", ".ts", ".tsx", ".html", ".css")):
            has_frontend = True

        if name.endswith(".py") and ("flask" in content or "@app.route" in content):
            has_backend = True

        if "fetch(" in content or "axios." in content or "@app.route" in content:
            has_api = True

        if (
            "jsonify" in content
            or "application/json" in content
            or "request.json" in content
        ):
            has_json = True

    architecture = []

    if has_frontend and has_backend:
        architecture.append(
            "- Frontend sends user input or uploaded code to the backend API."
        )
        architecture.append(
            "- Backend analyzes the code and generates structured documentation."
        )
        architecture.append(
            "- Frontend receives the response and displays the generated documentation."
        )

    if has_api:
        architecture.append("- API communication is used between application layers.")

    if has_json:
        architecture.append("- JSON is used for request and response data exchange.")

    if architecture:
        return "\n".join(architecture)

    return "- Architecture flow could not be clearly detected."


def generate_improvement_suggestions(uploaded_files):
    suggestions = []

    has_debug = False
    has_env = False
    has_tests = False
    has_readme = False
    has_requirements = False

    for file in uploaded_files:
        name = file["file_name"].lower()
        content = file["content"].lower()

        if "debug=true" in content or "debug = true" in content:
            has_debug = True

        if ".env" in name or "os.environ" in content or "dotenv" in content:
            has_env = True

        if "test" in name:
            has_tests = True

        if name.endswith("readme.md"):
            has_readme = True

        if name.endswith("requirements.txt") or name.endswith("package.json"):
            has_requirements = True

    if has_debug:
        suggestions.append("- Disable debug mode before production deployment.")

    if not has_env:
        suggestions.append(
            "- Add environment variable support for secrets and configuration."
        )

    if not has_tests:
        suggestions.append(
            "- Add automated tests to verify important application behavior."
        )

    if not has_readme:
        suggestions.append(
            "- Add a README file with setup, usage, and deployment instructions."
        )

    if not has_requirements:
        suggestions.append(
            "- Add dependency files such as requirements.txt or package.json."
        )

    if suggestions:
        return "\n".join(suggestions)

    return "- No major improvement suggestions detected."


def generate_documentation_quality_score(uploaded_files):
    score = 50

    has_readme = False
    has_dependencies = False
    has_routes = False
    has_security_issue = False
    has_multiple_files = len(uploaded_files) > 1

    for file in uploaded_files:
        name = file["file_name"].lower()
        content = file["content"].lower()

        if name.endswith("readme.md"):
            has_readme = True

        if name.endswith("requirements.txt") or name.endswith("package.json"):
            has_dependencies = True

        if "@app.route" in content:
            has_routes = True

        if "debug=true" in content or "debug = true" in content:
            has_security_issue = True

    if has_multiple_files:
        score += 10

    if has_routes:
        score += 15

    if has_dependencies:
        score += 10

    if has_readme:
        score += 10

    if has_security_issue:
        score -= 10

    if score >= 85:
        level = "Excellent"
    elif score >= 70:
        level = "Good"
    elif score >= 50:
        level = "Needs Improvement"
    else:
        level = "Poor"

    return "- Score: " + str(score) + "/100\n- Level: " + level

def detect_missing_project_files(uploaded_files):
    file_names = [file["file_name"].lower() for file in uploaded_files]

    missing = []

    has_readme = any(name.endswith("readme.md") for name in file_names)
    has_requirements = any(name.endswith("requirements.txt") for name in file_names)
    has_package_json = any(name.endswith("package.json") for name in file_names)
    has_env_example = any(name.endswith(".env.example") for name in file_names)
    has_tests = any("test" in name or "tests/" in name for name in file_names)

    if not has_readme:
        missing.append("- `README.md`: Missing project setup and usage documentation.")

    if not has_requirements and not has_package_json:
        missing.append("- `requirements.txt` or `package.json`: Missing dependency list.")

    if not has_env_example:
        missing.append("- `.env.example`: Missing environment variable example file.")

    if not has_tests:
        missing.append("- `tests/`: Missing automated test files.")

    if missing:
        return "\n".join(missing)

    return "- No major missing project files detected."

def generate_setup_checklist(uploaded_files):
    checklist = []

    file_names = [file["file_name"].lower() for file in uploaded_files]

    has_readme = any(name.endswith("readme.md") for name in file_names)
    has_requirements = any(name.endswith("requirements.txt") for name in file_names)
    has_package_json = any(name.endswith("package.json") for name in file_names)
    has_env_example = any(name.endswith(".env.example") for name in file_names)
    has_tests = any("test" in name or "tests/" in name for name in file_names)

    checklist.append("- [ ] Review generated documentation for accuracy.")

    if has_requirements or has_package_json:
        checklist.append("- [x] Dependency file is present.")
    else:
        checklist.append("- [ ] Add dependency file such as requirements.txt or package.json.")

    if has_readme:
        checklist.append("- [x] README file is present.")
    else:
        checklist.append("- [ ] Add README.md with setup and usage steps.")

    if has_env_example:
        checklist.append("- [x] Environment example file is present.")
    else:
        checklist.append("- [ ] Add .env.example for environment variables.")

    if has_tests:
        checklist.append("- [x] Test files are present.")
    else:
        checklist.append("- [ ] Add basic automated tests.")

    checklist.append("- [ ] Disable debug mode before production deployment.")
    checklist.append("- [ ] Test all API endpoints before release.")

    return "\n".join(checklist)


def generate_production_readiness_level(uploaded_files):
    score = 0

    file_names = [file["file_name"].lower() for file in uploaded_files]
    all_content = "\n".join([file["content"].lower() for file in uploaded_files])

    if any(name.endswith("readme.md") for name in file_names):
        score += 20

    if any(
        name.endswith("requirements.txt") or name.endswith("package.json")
        for name in file_names
    ):
        score += 20

    if any(name.endswith(".env.example") for name in file_names):
        score += 15

    if any("test" in name or "tests/" in name for name in file_names):
        score += 15

    if "@app.route" in all_content:
        score += 15

    if "debug=true" not in all_content and "debug = true" not in all_content:
        score += 15

    if score >= 80:
        level = "Production Ready"
    elif score >= 50:
        level = "Almost Ready"
    else:
        level = "Needs Work"

    return "- Score: " + str(score) + "/100\n- Level: " + level

def generate_technology_stack_summary(uploaded_files):
    frontend = set()
    backend = set()
    api = set()
    utilities = set()

    for file in uploaded_files:
        name = file["file_name"].lower()
        content = file["content"].lower()

        # Frontend
        if name.endswith((".js", ".jsx", ".ts", ".tsx")):
            frontend.add("JavaScript")

        if "import react" in content or "usestate" in content:
            frontend.add("React")

        if name.endswith(".css"):
            frontend.add("CSS")

        if name.endswith(".html"):
            frontend.add("HTML")

        # Backend
        if name.endswith(".py"):
            backend.add("Python")

        if "from flask import" in content:
            backend.add("Flask")

        # API
        if "@app.route" in content:
            api.add("REST API")

        if "jsonify" in content or "application/json" in content:
            api.add("JSON")

        # Utilities
        if "jspdf" in content:
            utilities.add("jsPDF")

        if "flask_cors" in content:
            utilities.add("flask_cors")

        if "filereader" in content:
            utilities.add("FileReader API")

    sections = []

    if frontend:
        sections.append(
            "Frontend\n" +
            "\n".join(["- " + item for item in sorted(frontend)])
        )

    if backend:
        sections.append(
            "Backend\n" +
            "\n".join(["- " + item for item in sorted(backend)])
        )

    if api:
        sections.append(
            "API\n" +
            "\n".join(["- " + item for item in sorted(api)])
        )

    if utilities:
        sections.append(
            "Utilities\n" +
            "\n".join(["- " + item for item in sorted(utilities)])
        )

    if sections:
        return "\n\n".join(sections)

    return "- Technology stack could not be clearly detected."

def generate_risk_analysis(uploaded_files):
    risks = []

    file_names = [file["file_name"].lower() for file in uploaded_files]
    all_content = "\n".join([file["content"].lower() for file in uploaded_files])

    if "debug=true" in all_content or "debug = true" in all_content:
        risks.append("- High: Debug mode is enabled and should be disabled before production.")

    if not any(name.endswith(".env.example") for name in file_names):
        risks.append("- Medium: No .env.example file found for environment configuration.")

    if not any(name.endswith("requirements.txt") or name.endswith("package.json") for name in file_names):
        risks.append("- Medium: Dependency file is missing, which may make setup harder.")

    if "cors(app)" in all_content:
        risks.append("- Medium: CORS is enabled globally. Review allowed origins before deployment.")

    if not any("test" in name or "tests/" in name for name in file_names):
        risks.append("- Medium: No automated test files detected.")

    if risks:
        return "\n".join(risks)

    return "- No major risks detected."

def detect_important_files(uploaded_files):
    important = []

    for file in uploaded_files:
        name = file["file_name"].lower()
        content = file["content"].lower()

        if name.endswith("app.py") or "flask(__name__)" in content or "from flask import" in content:
            important.append("- `" + file["file_name"] + "`: Possible Flask backend entry file.")

        elif name.endswith("manage.py"):
            important.append(
                "- `" + file["file_name"] + "`: Django project management file."
            )

        elif name.endswith("settings.py"):
            important.append(
                "- `" + file["file_name"] + "`: Django configuration/settings file."
            )

        elif name.endswith("urls.py"):
            important.append("- `" + file["file_name"] + "`: Django URL routing file.")

        elif name.endswith("views.py"):
            important.append(
                "- `"
                + file["file_name"]
                + "`: Django/Flask view or request handler file."
            )

        elif name.endswith("models.py"):
            important.append(
                "- `" + file["file_name"] + "`: Database model definition file."
            )

        elif name.endswith("package.json"):
            important.append(
                "- `"
                + file["file_name"]
                + "`: JavaScript project dependency and script configuration file."
            )

        elif (
            name.endswith("app.js")
            or name.endswith("app.jsx")
            or name.endswith("app.tsx")
        ):
            important.append(
                "- `"
                + file["file_name"]
                + "`: Main frontend application component/file."
            )

        elif name.endswith("index.js") or name.endswith("main.js"):
            important.append(
                "- `" + file["file_name"] + "`: JavaScript application entry file."
            )

        elif name.endswith("index.html"):
            important.append("- `" + file["file_name"] + "`: Main HTML entry page.")

        elif name.endswith("style.css") or name.endswith("app.css"):
            important.append("- `" + file["file_name"] + "`: Main stylesheet file.")

        elif name.endswith(".sql"):
            important.append("- `" + file["file_name"] + "`: SQL/database script file.")

    if important:
        return "\n".join(important)

    return "- No common important project files detected."


def detect_frameworks(uploaded_files):
    frameworks = []

    for file in uploaded_files:
        name = file["file_name"].lower()
        content = file["content"].lower()

        # Flask: real Flask imports/app initialization
        if (
            "from flask import" in content
            or "app = flask(__name__)" in content
            or "app=flask(__name__)" in content
        ):
            frameworks.append("Flask")

        # Django: detect from actual Django project files
        if (
            name.endswith("manage.py")
            or name.endswith("settings.py")
            or name.endswith("urls.py")
        ):
            if (
                "django" in content
                or name.endswith("settings.py")
                or name.endswith("manage.py")
            ):
                frameworks.append("Django")

        # React: detect from JS/JSX/TSX files only
        if name.endswith(".js") or name.endswith(".jsx") or name.endswith(".tsx"):
            if (
                "import react" in content
                or "from 'react'" in content
                or 'from "react"' in content
            ):
                frameworks.append("React")

        # React Native: detect from JS/JSX/TSX files only
        if name.endswith(".js") or name.endswith(".jsx") or name.endswith(".tsx"):
            if (
                "from 'react-native'" in content
                or 'from "react-native"' in content
                or "stylesheet.create" in content
            ):
                frameworks.append("React Native")

        # Flutter: detect from Dart files only
        if name.endswith(".dart"):
            if (
                "package:flutter" in content
                or "statelesswidget" in content
                or "statefulwidget" in content
            ):
                frameworks.append("Flutter")

        # Express: detect from JS/TS backend files only
        if name.endswith(".js") or name.endswith(".ts"):
            if (
                "require('express')" in content
                or 'require("express")' in content
                or "from 'express'" in content
                or 'from "express"' in content
            ):
                frameworks.append("Express.js")

        # Laravel: detect from PHP files only
        if name.endswith(".php"):
            if "laravel" in content or "artisan" in content:
                frameworks.append("Laravel")

        # ASP.NET Core: detect from C# project/code files only
        if name.endswith(".cs") or name.endswith(".csproj"):
            if (
                "microsoft.aspnetcore" in content
                or "webapplication.createbuilder" in content
            ):
                frameworks.append("ASP.NET Core")

    unique_frameworks = sorted(set(frameworks))

    if unique_frameworks:
        return "\n".join(["- " + framework for framework in unique_frameworks])

    return "- No common framework detected."


def generate_project_purpose(uploaded_files, languages):
    frameworks_text = detect_frameworks(uploaded_files).lower()
    language_set = set(languages)

    has_python = "Python" in language_set
    has_javascript = "JavaScript" in language_set
    has_react = "react" in frameworks_text
    has_flask = "flask" in frameworks_text
    has_django = "django" in frameworks_text
    has_sql = "SQL" in language_set
    has_html = "HTML" in language_set
    has_css = "CSS" in language_set

    if has_flask and has_javascript:
        return "This appears to be a Flask-based backend project with JavaScript code. It likely provides backend API routes and client-side or frontend logic."

    if has_flask:
        return "This appears to be a Flask backend application. It likely handles HTTP requests, API routes, and server-side logic."

    if has_django:
        return "This appears to be a Django web application. It likely includes configuration, URL routing, views, and backend application logic."

    if has_react:
        return "This appears to be a React frontend project. It likely contains reusable UI components and client-side application logic."

    if has_python and has_javascript:
        return "This appears to be a mixed Python and JavaScript project. It may include backend logic along with frontend or client-side code."

    if has_html and has_css and has_javascript:
        return (
            "This appears to be a frontend web project using HTML, CSS, and JavaScript."
        )

    if has_sql:
        return "This project includes SQL/database scripts, likely used for creating, querying, or managing database data."

    if has_python:
        return "This appears to be a Python project containing application logic, helper functions, or backend code."

    if has_javascript:
        return "This appears to be a JavaScript project containing client-side or server-side scripting logic."

    return "This appears to be a general code project containing multiple source files."


def detect_entry_points(uploaded_files):
    entry_points = []

    for file in uploaded_files:
        name = file["file_name"].lower()
        content = file["content"].lower()

        if "from flask import" in content and (
            "app.run" in content or "flask(__name__)" in content
        ):
            entry_points.append(
                "- `" + file["file_name"] + "`: Flask application entry point."
            )

        elif name.endswith("manage.py"):
            entry_points.append(
                "- `"
                + file["file_name"]
                + "`: Django project command-line entry point."
            )

        elif name.endswith("main.py"):
            entry_points.append("- `" + file["file_name"] + "`: Python main script.")

        elif name.endswith("index.js") or name.endswith("main.js"):
            entry_points.append(
                "- `" + file["file_name"] + "`: JavaScript application entry file."
            )

        elif (
            name.endswith("app.js")
            or name.endswith("app.jsx")
            or name.endswith("app.tsx")
        ):
            entry_points.append(
                "- `" + file["file_name"] + "`: Main frontend application file."
            )

        elif name.endswith("index.html"):
            entry_points.append("- `" + file["file_name"] + "`: Main HTML entry page.")

        elif name.endswith("main.dart"):
            entry_points.append(
                "- `" + file["file_name"] + "`: Flutter/Dart application entry file."
            )

        elif name.endswith("program.cs"):
            entry_points.append(
                "- `" + file["file_name"] + "`: C#/.NET application entry file."
            )

    if entry_points:
        return "\n".join(entry_points)

    return "- No clear entry point detected."


def detect_dependencies(uploaded_files):
    dependencies = []

    for file in uploaded_files:
        name = file["file_name"].lower()
        content = file["content"]

        # Python imports
        if name.endswith(".py"):
            lines = content.splitlines()

            for line in lines:
                clean_line = line.strip()

                if clean_line.startswith("import "):
                    package = (
                        clean_line.replace("import ", "")
                        .split(" ")[0]
                        .split(".")[0]
                        .strip()
                    )
                    if package:
                        dependencies.append(package)

                elif clean_line.startswith("from "):
                    package = (
                        clean_line.replace("from ", "")
                        .split(" import")[0]
                        .split(".")[0]
                        .strip()
                    )
                    if package:
                        dependencies.append(package)

        # JavaScript / TypeScript imports
        if name.endswith((".js", ".jsx", ".ts", ".tsx")):
            lines = content.splitlines()

            for line in lines:
                clean_line = line.strip()

                if clean_line.startswith("import ") and " from " in clean_line:
                    package = (
                        clean_line.split(" from ")[1]
                        .replace(";", "")
                        .replace('"', "")
                        .replace("'", "")
                        .strip()
                    )
                    if package and not package.startswith("."):
                        dependencies.append(package)

                if "require(" in clean_line:
                    package = (
                        clean_line.split("require(")[1]
                        .split(")")[0]
                        .replace('"', "")
                        .replace("'", "")
                        .strip()
                    )
                    if package and not package.startswith("."):
                        dependencies.append(package)

    unique_dependencies = sorted(set(dependencies))

    if unique_dependencies:
        return "\n".join(["- `" + dep + "`" for dep in unique_dependencies])

    return "- No common dependencies detected."


def detect_security_notes(uploaded_files):
    notes = []

    for file in uploaded_files:
        name = file["file_name"]
        lower_name = name.lower()
        content = file["content"]
        lower_content = content.lower()

        if (
            "api_key" in lower_content
            or "secret_key" in lower_content
            or "password =" in lower_content
        ):
            notes.append(
                "- `"
                + name
                + "`: Possible hardcoded secret, API key, or password detected."
            )

        if "sk-" in content or "sk-proj-" in content:
            notes.append("- `" + name + "`: Possible OpenAI/API key pattern detected.")

        if "debug=true" in lower_content or "app.run(debug=true)" in lower_content:
            notes.append(
                "- `"
                + name
                + "`: Debug mode appears to be enabled. Disable it in production."
            )

        if lower_name.endswith(".py"):
            if "execute(" in lower_content and "+" in content:
                notes.append(
                    "- `"
                    + name
                    + "`: SQL query may be built using string concatenation. Use parameterized queries."
                )

        if lower_name.endswith((".js", ".jsx", ".ts", ".tsx")):
            if "dangerouslysetinnerhtml" in lower_content:
                notes.append(
                    "- `"
                    + name
                    + "`: dangerouslySetInnerHTML can create XSS risk if content is not sanitized."
                )

        if lower_name.endswith(".php"):
            if "$_get" in lower_content or "$_post" in lower_content:
                notes.append(
                    "- `"
                    + name
                    + "`: User input is used. Validate and sanitize request data."
                )

    unique_notes = []
    for note in notes:
        if note not in unique_notes:
            unique_notes.append(note)

    if unique_notes:
        return "\n".join(unique_notes)

    return "- No obvious security risks detected from static scan."


def generate_run_instructions(uploaded_files, languages):
    instructions = []

    file_names = [file["file_name"].lower() for file in uploaded_files]
    language_set = set(languages)

    has_flask = any(
        "from flask import" in file["content"].lower()
        or "flask(__name__)" in file["content"].lower()
        for file in uploaded_files
    )

    has_django = any(
        name.endswith("manage.py") or name.endswith("settings.py")
        for name in file_names
    )

    has_package_json = any(name.endswith("package.json") for name in file_names)

    has_html = "HTML" in language_set
    has_python = "Python" in language_set
    has_js = (
        "JavaScript" in language_set
        or "React" in language_set
        or "TypeScript" in language_set
    )

    if has_flask:
        instructions.append(
            "- Flask detected: Install dependencies, then run the backend using `python app.py`."
        )

    if has_django:
        instructions.append(
            "- Django detected: Install dependencies, then run the project using `python manage.py runserver`."
        )

    if has_package_json:
        instructions.append(
            "- Node/React project detected: Run `npm install`, then start the project using `npm start` or `npm run dev`."
        )

    if has_html and not has_package_json:
        instructions.append(
            "- Static HTML project detected: Open the main `.html` file in a browser."
        )

    if has_python and not has_flask and not has_django:
        instructions.append(
            "- Python files detected: Run a Python file using `python filename.py`."
        )

    if has_js and not has_package_json:
        instructions.append(
            "- JavaScript files detected: Run in browser console or with Node.js using `node filename.js`."
        )

    if not instructions:
        instructions.append(
            "- No clear run command detected. Check the project README or main entry file."
        )

    return "\n".join(instructions)


def generate_project_summary(uploaded_files, languages):
    file_names = [file["file_name"] for file in uploaded_files]
    unique_languages = sorted(set(languages))

    files_text = "\n".join(["- " + name for name in file_names])
    languages_text = ", ".join(unique_languages)
    project_type = detect_project_type(uploaded_files, languages)
    project_metrics = get_project_metrics(uploaded_files)
    important_files = detect_important_files(uploaded_files)
    frameworks = detect_frameworks(uploaded_files)
    entry_points = detect_entry_points(uploaded_files)
    dependencies = detect_dependencies(uploaded_files)
    project_purpose = generate_project_purpose(uploaded_files, languages)
    security_notes = detect_security_notes(uploaded_files)
    run_instructions = generate_run_instructions(uploaded_files, languages)
    api_summary = generate_api_endpoint_summary(uploaded_files)
    backend_workflow = generate_backend_workflow_summary(uploaded_files)
    frontend_workflow = generate_frontend_workflow_summary(uploaded_files)
    architecture_summary = generate_architecture_summary(uploaded_files)
    improvement_suggestions = generate_improvement_suggestions(uploaded_files)
    quality_score = generate_documentation_quality_score(uploaded_files)
    missing_files = detect_missing_project_files(uploaded_files)
    setup_checklist = generate_setup_checklist(uploaded_files)
    production_readiness = generate_production_readiness_level(uploaded_files)
    technology_stack = generate_technology_stack_summary(uploaded_files)
    risk_analysis = generate_risk_analysis(uploaded_files)
    ai_status = (
        "- Enabled"
        if is_ai_enabled()
        else "- Not enabled. Using rule-based documentation engine."
    )

    flask_routes = []

    for file in uploaded_files:
        if file["file_name"].lower().endswith(".py"):
            routes = detect_flask_routes(file["content"])
            if routes != "- No Flask routes detected.":
                flask_routes.append("### " + file["file_name"])
                flask_routes.append(routes)

    if flask_routes:
        flask_routes_text = "\n".join(flask_routes)
    else:
        flask_routes_text = "- No Flask routes detected."

    summary_lines = [
        "PROJECT SUMMARY",
        "",
        "Total Files",
        "- " + str(len(uploaded_files)),
        "",
        "Detected Languages",
        "- " + languages_text,
        "",
        "Project Type",
        "- " + project_type,
        "",
        "Project Metrics",
        "- Total Lines: " + str(project_metrics["total_lines"]),
        "- Non-empty Lines: " + str(project_metrics["non_empty_lines"]),
        "",
        "Files Included",
        files_text,
        "",
        "Folder Structure",
        generate_folder_structure(uploaded_files),
        "",
        "Important Files",
        important_files,
        "",
        "Detected Frameworks",
        frameworks,
        "",
        "Detected Flask Routes",
        flask_routes_text,
        "",
        "API Endpoint Summary",
        api_summary,
        "",
        "Backend Workflow Summary",
        backend_workflow,
        "",
        "Frontend Workflow Summary",
        frontend_workflow,
        "",
        "Architecture Summary",
        architecture_summary,
        "",
        "Improvement Suggestions",
        improvement_suggestions,
        "",
        "Documentation Quality Score",
        quality_score,
        "",
        "Missing Project Files",
        missing_files,
        "",
        "Setup Checklist",
        setup_checklist,
        "",
        "Production Readiness Level",
        production_readiness,
        "",
        "Technology Stack Summary",
        technology_stack,
        "",
        "Risk Analysis",
        risk_analysis,
        "",
        "AI Engine Status",
        ai_status,
        "",
        "Project Purpose",
        "- " + project_purpose,
        "",
        "Entry Points",
        entry_points,
        "",
        "Detected Dependencies",
        dependencies,
        "",
        "Security Notes",
        security_notes,
        "",
        "Run Instructions",
        run_instructions,
        "",
        "Overview",
        "This uploaded project or file group contains multiple source files. The system analyzed each file separately and generated documentation based on the detected language and structure.",
        "",
        "========================================",
    ]

    return "\n".join(summary_lines)


@app.route("/health", methods=["GET"])
def health_check():
    return jsonify({
        "ok": True,
        "status": "healthy",
        "service": "AI Doc Assistant API",
        "ai_enabled": is_ai_enabled(),
    })


@app.route("/generate-doc", methods=["POST"])
def generate_doc():
    try:
        data = request.get_json(silent=True) or {}
        code = data.get("code", "")
        file_name = data.get("fileName", "")

        if not isinstance(code, str) or not code.strip():
            return jsonify({"ok": False, "error": "No code provided."}), 400

        if len(code) > MAX_CODE_CHARS:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "Uploaded code is too large. Please upload a smaller project or increase MAX_CODE_CHARS.",
                    }
                ),
                413,
            )

        if not isinstance(file_name, str):
            file_name = "uploaded-code.txt"

        uploaded_files = split_multiple_files(code)

        if not uploaded_files:
            uploaded_files = [
                {"file_name": file_name or "uploaded-code.txt", "content": code}
            ]

        all_docs = []
        languages = []

        for file in uploaded_files:
            language = detect_language(file["content"], file["file_name"])
            languages.append(language)
            metrics = get_file_metrics(file["content"])

            if is_ai_enabled():
                ai_result = generate_ai_documentation(
                    file["content"], file["file_name"]
                )

                if ai_result["success"]:
                    single_doc = ai_result["doc"]
                else:
                    fallback_doc, fallback_language = analyze_single_file(
                        file["content"], file["file_name"]
                    )

                    single_doc = (
                        "AI Engine Notice\n"
                        "- AI generation failed, so rule-based documentation was used instead.\n"
                        "- Reason: " + ai_result["error"] + "\n\n" + fallback_doc
                    )
            else:
                single_doc, language = analyze_single_file(
                    file["content"], file["file_name"]
                )

            file_doc = "\n".join(
                [
                    "========================================",
                    "FILE: " + file["file_name"],
                    "LANGUAGE: " + language,
                    "========================================",
                    "",
                    "File Metrics",
                    "- Total Lines: " + str(metrics["total_lines"]),
                    "- Non-empty Lines: " + str(metrics["non_empty_lines"]),
                    "",
                    single_doc,
                ]
            )

            all_docs.append(file_doc)

        project_summary = ""

        if len(uploaded_files) > 1:
            project_summary = generate_project_summary(uploaded_files, languages)
            final_doc = project_summary + "\n\n" + "\n\n".join(all_docs)
        else:
            final_doc = "\n\n".join(all_docs)

        language_summary = ", ".join(sorted(set(languages)))

        return jsonify(
            {
                "ok": True,
                "doc": final_doc,
                "language": language_summary,
                "fileCount": len(uploaded_files),
                "aiEnabled": is_ai_enabled(),
            }
        )

    except SyntaxError as error:
        return (
            jsonify({"ok": False, "error": "Syntax error in code: " + str(error)}),
            400,
        )

    except Exception as error:
        logger.exception("Unexpected error while generating documentation")
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "Unexpected server error while generating documentation.",
                }
            ),
            500,
        )


@app.errorhandler(413)
def request_entity_too_large(error):
    return jsonify({"ok": False, "error": "Uploaded payload is too large."}), 413


@app.errorhandler(404)
def not_found(error):
    return jsonify({"ok": False, "error": "Endpoint not found."}), 404


@app.errorhandler(500)
def internal_error(error):
    return jsonify({"ok": False, "error": "Internal server error."}), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)
