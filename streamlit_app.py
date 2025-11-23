import streamlit as st
from utility import check_password
import os
from openai import OpenAI
import json
from datetime import datetime
import PyPDF2
from io import BytesIO
import io
import re
import time


# Do not continue if check_password is not True.  
if not check_password():  
    st.stop()

# Page configuration
st.set_page_config(
    page_title="Automated Contribution Deferment Assessment System",
    page_icon="📋",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Initialize session state
if "messages" not in st.session_state:
    st.session_state.messages = []
if "logged_in" not in st.session_state:
    st.session_state.logged_in = True
if "current_page" not in st.session_state:
    st.session_state.current_page = "Home"
if "form_key" not in st.session_state:
    st.session_state.form_key = 0

# Get request count from vector database
def get_request_count_from_db():
    """Count total requests from vector database"""
    try:
        count = 0
        with open('assessments_log.json', 'r') as f:
            for line in f:
                if line.strip():
                    count += 1
        return count
    except FileNotFoundError:
        return 0

# Load user profiles
def load_user_profiles():
    try:
        with open('user_profiles.json', 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        st.error("user_profiles.json not found. Please create the file.")
        return {}

# Load OpenAI API key from environment
def get_api_key():
    api_key = st.secrets["OPENAI_API_KEY"]
    if not api_key:
        st.error("OpenAI API key not found in environment variables. Please set OPENAI_API_KEY.")
        return None
    return api_key

# Extract text from PDF
def extract_text_from_pdf(pdf_file):
    pdf_reader = PyPDF2.PdfReader(BytesIO(pdf_file.read()))
    text = ""
    for page in pdf_reader.pages:
        text += page.extract_text()
    return text

# Extract NRIC from text
def extract_nric_from_text(text):
    """Extract NRIC numbers from text using regex"""
    if not text:
        return []
    
    # Pattern to match NRIC: S/T/F/G followed by 7 digits and a letter
    pattern = r'\b[STFG]\d{7}[A-Z]\b'
    matches = re.findall(pattern, text.upper())
    
    # Return unique NRICs found
    return list(set(matches))

# Validate NRIC matching
def validate_nric_match(form_nric, pdf_text):
    """
    Check if the NRIC from form matches any NRIC found in the PDF
    Returns: (is_valid, message, found_nrics)
    """
    if not pdf_text:
        # No PDF provided, validation passes
        return True, "No PDF document provided", []
    
    found_nrics = extract_nric_from_text(pdf_text)
    
    if not found_nrics:
        # No NRIC found in PDF, flag as warning
        return True, "⚠ Warning: No NRIC found in PDF document", []
    
    form_nric_upper = form_nric.upper()
    
    if form_nric_upper in found_nrics:
        return True, f"✓ NRIC matches document (Found: {form_nric_upper})", found_nrics
    else:
        return False, f"✗ NRIC mismatch: Form NRIC ({form_nric_upper}) does not match PDF NRICs ({', '.join(found_nrics)})", found_nrics

# Check if NRIC exists in user profiles
def check_nric_in_profiles(nric, user_profiles):
    """
    Check if NRIC exists in user profiles JSON
    Returns: (exists, message)
    """
    nric_upper = nric.upper()
    
    if nric_upper in user_profiles:
        return True, f"✓ NRIC found in user profiles database"
    else:
        return False, f"✗ NRIC not found in user profiles database"

# Deferment keywords
DEFERMENT_KEYWORDS = [
    "defer", "extend", "cannot pay", "exemption", "postpone", 
    "temporary reduction", "waiver", "deferment", "delay", 
    "suspend", "pause", "unable to pay"
]

# Check if request is deferment-related
def is_deferment_request(text):
    text_lower = text.lower()
    return any(keyword in text_lower for keyword in DEFERMENT_KEYWORDS)

# Validate NRIC format
def validate_nric(nric):
    """Validate NRIC format (basic validation)"""
    if not nric:
        return False
    nric = nric.strip().upper()
    # Basic NRIC pattern: S/T/F/G followed by 7 digits and a letter
    pattern = r'^[STFG]\d{7}[A-Z]$'
    return bool(re.match(pattern, nric))

# Parse duration from text
def extract_duration_months(text):
    """Extract duration in months from text"""
    text_lower = text.lower()
    
    # Look for explicit month mentions
    month_patterns = [
        (r'(\d+)\s*months?', 1),
        (r'(\d+)\s*-\s*months?', 1),
        (r'approximately\s*(\d+)\s*months?', 1),
        (r'about\s*(\d+)\s*months?', 1),
        (r'for\s*(\d+)\s*months?', 1),
    ]
    
    for pattern, group_idx in month_patterns:
        match = re.search(pattern, text_lower)
        if match:
            return int(match.group(group_idx))
    
    # Look for year mentions (convert to months)
    year_match = re.search(r'(\d+)\s*years?', text_lower)
    if year_match:
        return int(year_match.group(1)) * 12
    
    # Default duration based on keywords
    if 'immediate' in text_lower or 'urgent' in text_lower:
        return 1
    
    return 0

# Determine approval authority
def get_approval_authority(duration_months):
    if duration_months <= 3:
        return "Executive/Assistant Manager/Inspector and above"
    elif duration_months <= 6:
        return "Manager and above"
    elif duration_months <= 12:
        return "Assistant Director and above"
    else:
        return "Director and above"

# Analyze deferment request with NRIC validation
def analyze_deferment_request(client, nric, user_input, pdf_text="", user_profiles=None):
    """
    Enhanced analysis with NRIC validation checks
    """
    combined_input = f"{user_input}\n\nAdditional Information from PDF:\n{pdf_text}" if pdf_text else user_input
    
    # Initialize validation results
    validation_results = {
        "nric_format_valid": validate_nric(nric),
        "nric_in_profiles": False,
        "nric_matches_pdf": True,
        "pdf_nrics_found": [],
        "can_grant": True,
        "validation_messages": []
    }
    
    # Check 1: NRIC format validation
    if not validation_results["nric_format_valid"]:
        validation_results["can_grant"] = False
        validation_results["validation_messages"].append("✗ Invalid NRIC format")
    
    # Check 2: NRIC exists in user profiles
    if user_profiles:
        nric_exists, message = check_nric_in_profiles(nric, user_profiles)
        validation_results["nric_in_profiles"] = nric_exists
        validation_results["validation_messages"].append(message)
        
        if not nric_exists:
            validation_results["can_grant"] = False
    
    # Check 3: NRIC matches PDF (if PDF provided)
    if pdf_text:
        nric_matches, match_message, found_nrics = validate_nric_match(nric, pdf_text)
        validation_results["nric_matches_pdf"] = nric_matches
        validation_results["pdf_nrics_found"] = found_nrics
        validation_results["validation_messages"].append(match_message)
        
        if not nric_matches:
            validation_results["can_grant"] = False
    
    # If validation fails, return early with validation results
    if not validation_results["can_grant"]:
        return {
            "is_deferment": False,
            "validation_failed": True,
            "validation_results": validation_results,
            "summary": "Deferment request CANNOT be granted due to validation failures. See validation results for details.",
            "extracted_info": {"nric": nric}
        }
    
    # Step 1: Check if it's a deferment request
    if not is_deferment_request(combined_input):
        return {
            "is_deferment": False,
            "validation_failed": False,
            "validation_results": validation_results,
            "summary": "This request does not appear to be related to contribution deferment. No deferment-related keywords were detected in the submission.",
            "extracted_info": {}
        }
    
    # Step 2: Extract basic information locally
    duration_months = extract_duration_months(combined_input)
    
    # Step 3: Use AI to extract detailed information
    extraction_prompt = f"""
    Analyze the following deferment request and provide a brief summary.
    
    Please extract and describe concisely:
    1. The duration of deferment requested (in months)
    2. The primary reason for deferment
    3. Start date if mentioned
    4. Key supporting details
    
    Request text:
    {combined_input}
    
    Provide your response as a brief paragraph (max 150 words) describing what you found.
    Be specific about numbers, dates, and reasons mentioned.
    """
    
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": extraction_prompt}],
        temperature=0.3
    )
    
    ai_extracted_text = response.choices[0].message.content
    
    # If duration not found, try to extract from AI response
    if duration_months == 0:
        duration_months = extract_duration_months(ai_extracted_text)
    
    # Create extracted info dictionary
    extracted_info = {
        "nric": nric,
        "duration_months": duration_months,
        "ai_analysis": ai_extracted_text
    }
    
    # Step 4: Get user profile
    user_profile = user_profiles.get(nric.upper(), {}) if user_profiles else {}
    
    # Step 5: Generate assessment with validation context
    assessment_prompt = f"""
    You are a deferment assessment officer. Create a CONCISE assessment report (max 200 words).
    
    VALIDATION STATUS:
    - NRIC Format: Valid ✓
    - NRIC in Database: Valid ✓
    - NRIC matches PDF: Valid ✓
    
    EXTRACTED INFORMATION:
    {ai_extracted_text}
    
    USER PROFILE:
    {json.dumps(user_profile, indent=2) if user_profile else "No user profile found for this NRIC."}
    
    REQUESTED DURATION: {duration_months} months
    
    Provide a SHORT assessment with:
    1. Brief summary of request
    2. Recommendation: GRANT or DENY with key reason
    3. Recommended duration (if granted)
    4. Main condition or requirement
    
    DO NOT include approval authority information in your response.
    Keep it concise and professional. Maximum 200 words total.
    """
    
    assessment_response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": assessment_prompt}],
        temperature=0.5
    )
    
    detailed_summary = assessment_response.choices[0].message.content
    
    return {
        "is_deferment": True,
        "validation_failed": False,
        "validation_results": validation_results,
        "extracted_info": extracted_info,
        "user_profile": user_profile,
        "summary": detailed_summary,
        "approval_authority": get_approval_authority(duration_months)
    }

# Store in vector DB
def store_in_vector_db(assessment_result, reviewer_comments):
    """Store assessment with reviewer comments in vector database"""
    timestamp = datetime.now().isoformat()
    record = {
        "timestamp": timestamp,
        "assessment": assessment_result,
        "reviewer_comments": reviewer_comments,
        "nric": assessment_result.get("extracted_info", {}).get("nric", "Unknown")
    }
    
    try:
        with open('assessments_log.json', 'a') as f:
            f.write(json.dumps(record) + '\n')
        return True
    except Exception as e:
        st.error(f"Error storing assessment: {str(e)}")
        return False

# Sidebar Navigation
def sidebar_navigation():
    with st.sidebar:
        st.title("Navigation")
        
        if st.button("🏠 Home", use_container_width=True):
            st.session_state.current_page = "Home"
            st.rerun()
        
        if st.button("ℹ About Us", use_container_width=True):
            st.session_state.current_page = "About Us"
            st.rerun()
        
        if st.button("📊 Methodology", use_container_width=True):
            st.session_state.current_page = "Methodology"
            st.rerun()
        
        st.divider()
        
        if st.button("🚪 Logout", use_container_width=True):
            st.session_state.logged_in = False
            st.session_state.clear()
            st.rerun()
        
        st.divider()
        # Get count from vector database
        total_requests = get_request_count_from_db()
        st.metric("Total Requests", total_requests)

# Home Page
def home_page():
    st.title("📋 Automated Contribution Deferment Assessment System")
    
    api_key = get_api_key()
    if not api_key:
        return
    
    client = OpenAI(api_key=api_key)
    user_profiles = load_user_profiles()
    
    col1, col2 = st.columns([3, 1])
    
    with col1:
        st.subheader("Submit Deferment Request")
        
        # NRIC input field
        nric_input = st.text_input(
            "NRIC Number *",
            placeholder="e.g., S1234567D",
            help="Enter NRIC in format: S/T/F/G followed by 7 digits and a letter",
            max_chars=9,
            key=f"nric_input_{st.session_state.form_key}"
        )
        
        # Validate NRIC
        nric_valid = False
        if nric_input:
            if validate_nric(nric_input):
                st.success("✓ Valid NRIC format")
                nric_valid = True
                
                # Check if NRIC exists in profiles
                nric_exists, _ = check_nric_in_profiles(nric_input, user_profiles)
                if nric_exists:
                    st.success("✓ NRIC found in user profiles database")
                else:
                    st.error("✗ NRIC not found in user profiles database - deferment cannot be granted")
            else:
                st.error("✗ Invalid NRIC format. Please use format: S1234567D")
        
        # Text input
        user_input = st.text_area(
            "Enter your deferment request details:",
            height=200,
            placeholder="Please provide details about your deferment request including reason and duration...",
            key=f"user_input_{st.session_state.form_key}"
        )
        
        # PDF upload
        uploaded_file = st.file_uploader(
            "Upload supporting documents (PDF)", 
            type=['pdf'],
            key=f"pdf_upload_{st.session_state.form_key}"
        )
        pdf_text = ""
        
        if uploaded_file:
            pdf_text = extract_text_from_pdf(uploaded_file)
            
            # Check NRIC match if both NRIC and PDF are provided
            if nric_input and nric_valid:
                nric_matches, match_message, found_nrics = validate_nric_match(nric_input, pdf_text)
                
                if found_nrics:
                    if nric_matches:
                        st.success(match_message)
                    else:
                        st.error(match_message)
                        st.error("⚠ Deferment cannot be granted due to NRIC mismatch")
                else:
                    st.warning(match_message)
            
            with st.expander("View extracted PDF content"):
                st.text(pdf_text[:1000] + "..." if len(pdf_text) > 1000 else pdf_text)
        
        # Analyze button
        analyze_button = st.button(
            "🔍 Analyze Request", 
            type="primary", 
            use_container_width=True,
            disabled=not (nric_valid and user_input)
        )
        
        # Analysis
        if analyze_button and nric_valid and user_input:
            with st.spinner("Analyzing your request..."):
                result = analyze_deferment_request(
                    client, 
                    nric_input.upper(), 
                    user_input, 
                    pdf_text,
                    user_profiles
                )
                st.session_state.current_assessment = result
                st.rerun()
        
        # Display results
        if hasattr(st.session_state, 'current_assessment'):
            result = st.session_state.current_assessment
            
            st.divider()
            st.subheader("📄 Assessment Results")
            
            # Display Validation Results First
            if "validation_results" in result:
                validation = result["validation_results"]
                
                with st.expander("🔒 NRIC Validation Results", expanded=True):
                    if validation["can_grant"]:
                        st.success("✓ All validation checks passed")
                    else:
                        st.error("✗ Validation failed - Deferment CANNOT be granted")
                    
                    st.write("*Validation Checks:*")
                    for message in validation["validation_messages"]:
                        if "✓" in message:
                            st.success(message)
                        elif "✗" in message:
                            st.error(message)
                        else:
                            st.warning(message)
                    
                    if validation["pdf_nrics_found"]:
                        st.info(f"*NRICs found in PDF:* {', '.join(validation['pdf_nrics_found'])}")
            
            # If validation failed, stop here
            if result.get("validation_failed", False):
                st.error("⛔ *DEFERMENT REQUEST REJECTED*")
                st.error(result["summary"])
                
                # Still allow reviewer comments for rejected requests
                st.divider()
                st.subheader("✍ Reviewer's Opinion")
                reviewer_comments = st.text_area(
                    "Enter your review and opinion on this validation failure:",
                    height=150,
                    placeholder="Document the reason for rejection and any recommendations...",
                    key="reviewer_input"
                )
                
                if reviewer_comments:
                    st.session_state.reviewer_comments = reviewer_comments
                
                # Submit button for rejected requests
                st.divider()
                submit_button = st.button(
                    "✅ Submit Assessment (Rejected)", 
                    type="secondary",
                    use_container_width=True
                )
                
                if submit_button:
                    if not reviewer_comments:
                        st.error("⚠ Please provide your reviewer opinion before submitting.")
                    else:
                        if store_in_vector_db(st.session_state.current_assessment, reviewer_comments):
                            st.success("✓ Rejected assessment recorded successfully!")
                            
                            # Clear session state
                            if 'current_assessment' in st.session_state:
                                del st.session_state['current_assessment']
                            if 'reviewer_comments' in st.session_state:
                                del st.session_state['reviewer_comments']
                            
                            st.session_state.form_key += 1
                            time.sleep(1)
                            st.rerun()
                
                return  # Stop processing if validation failed
            
            # Continue with normal deferment processing
            if result["is_deferment"]:
                st.success("✓ Deferment request identified and validation passed")
                
                # Extracted Information
                with st.expander("📋 Request Information", expanded=True):
                    info = result["extracted_info"]
                    st.write(f"*NRIC:* {info['nric']}")
                    st.write(f"*Requested Duration:* {info['duration_months']} months")
                    st.write("\n*Request Summary:*")
                    st.write(info['ai_analysis'])
                
                # User Profile
                if result["user_profile"]:
                    with st.expander("👤 User Profile Information", expanded=False):
                        profile = result["user_profile"]
                        col_p1, col_p2 = st.columns(2)
                        with col_p1:
                            st.write(f"*Name:* {profile.get('name', 'N/A')}")
                            st.write(f"*Age:* {profile.get('age', 'N/A')}")
                            st.write(f"*Position:* {profile.get('position', 'N/A')}")
                        with col_p2:
                            st.write(f"*Department:* {profile.get('department', 'N/A')}")
                            st.write(f"*Retirement Savings Met:* {'Yes' if profile.get('retirement_savings_met') else 'No'}")
                            st.write(f"*Receiving Financial Aid:* {'Yes' if profile.get('receiving_financial_aid') else 'No'}")
                else:
                    st.warning("⚠ No user profile found for this NRIC")
                
                # AI Assessment Summary
                with st.expander("📊 Assessment Summary", expanded=True):
                    st.markdown(result["summary"])
                
                # Approval Authority
                st.info(f"*Approval Authority Required:* {result['approval_authority']}")
                
            else:
                # Non-deferment scenario
                st.warning("⚠ This does not appear to be a deferment request")
                st.write(result["summary"])
            
            # Reviewer Comments Section
            st.divider()
            st.subheader("✍ Reviewer's Opinion")
            reviewer_comments = st.text_area(
                "Enter your review and opinion on this assessment:",
                height=150,
                placeholder="Provide your professional opinion, any additional considerations, or approval/rejection notes...",
                key="reviewer_input"
            )
            
            if reviewer_comments:
                st.session_state.reviewer_comments = reviewer_comments
            
            # Submit Assessment button
            st.divider()
            submit_button = st.button(
                "✅ Submit Assessment", 
                type="primary",
                use_container_width=True
            )
            
            # Submit to vector DB
            if submit_button:
                if not reviewer_comments:
                    st.error("⚠ Please provide your reviewer opinion before submitting.")
                else:
                    if store_in_vector_db(st.session_state.current_assessment, reviewer_comments):
                        st.success("✓ Assessment submitted successfully!")
                        
                        # Clear session state
                        if 'current_assessment' in st.session_state:
                            del st.session_state['current_assessment']
                        if 'reviewer_comments' in st.session_state:
                            del st.session_state['reviewer_comments']
                        
                        st.session_state.form_key += 1
                        time.sleep(1)
                        st.rerun()

# About Us Page
def about_us_page():
    st.title("ℹ About Us")
    
    st.markdown("""
    ## Automated Contribution Deferment Assessment System
    
    ### 📋 Project Overview
    The Automated Contribution Deferment Assessment System is an AI-powered platform designed to revolutionize 
    the way contribution deferment requests are processed and evaluated. By leveraging cutting-edge Natural 
    Language Processing (NLP) and machine learning technologies, this system reduces manual effort, increases 
    accuracy, and ensures consistency in decision-making across all deferment requests.
    """)
    
    st.divider()
    
    # Project Scope
    st.subheader("🎯 Project Scope")
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("""
        *In Scope:*
        - Automated analysis of deferment requests
        - NRIC-based user identification and validation
        - Natural language processing of request text
        - PDF document extraction and analysis
        - User profile cross-referencing
        - AI-powered recommendation generation
        - Human reviewer oversight integration
        - Audit trail and data persistence
        - Request classification and categorization
        - Duration extraction and validation
        """)
    
    with col2:
        st.markdown("""
        *Out of Scope:*
        - Final approval authority (human decision required)
        - Payment processing and financial transactions
        - Direct database modifications
        - Email notifications and communications
        - Integration with external HR systems
        - Automatic approval without human review
        - Real-time chat support
        - Mobile application development
        - Multi-language support (English only)
        - Historical data migration
        """)
    
    st.divider()
    
    # Objectives
    st.subheader("🎯 Project Objectives")
    
    objectives = [
        {
            "title": "Efficiency Improvement",
            "description": "Reduce processing time for deferment requests from hours to minutes by automating initial assessment and information extraction.",
            "icon": "⚡"
        },
        {
            "title": "Consistency in Decision-Making",
            "description": "Ensure all requests are evaluated using standardized criteria and policies, eliminating subjective bias in initial assessments.",
            "icon": "⚖"
        },
        {
            "title": "Enhanced Accuracy",
            "description": "Leverage AI to accurately extract key information, identify patterns, and cross-reference user profiles for comprehensive evaluation.",
            "icon": "🎯"
        },
        {
            "title": "Human Oversight Integration",
            "description": "Maintain human judgment in the decision-making process while providing AI-assisted recommendations and insights.",
            "icon": "👥"
        },
        {
            "title": "Comprehensive Audit Trail",
            "description": "Create detailed records of all assessments, reviewer comments, and decisions for compliance and review purposes.",
            "icon": "📝"
        },
        {
            "title": "Scalability",
            "description": "Handle increasing volumes of requests without proportional increase in processing time or human resources.",
            "icon": "📈"
        }
    ]
    
    for obj in objectives:
        with st.container():
            st.markdown(f"{obj['icon']} {obj['title']}")
            st.write(obj['description'])
            st.write("")
    
    st.divider()
    
    # Data Sources
    st.subheader("📊 Data Sources")
    
    st.markdown("""
    The system integrates multiple data sources to provide comprehensive assessment capabilities:
    """)
    
    data_sources = {
        "User Input (Text)": {
            "description": "Free-form text describing the deferment request, including reason, duration, and circumstances",
            "format": "Plain text, natural language",
            "usage": "Primary source for request analysis and keyword detection"
        },
        "PDF Documents": {
            "description": "Supporting documentation such as medical certificates, financial statements, or official letters",
            "format": "PDF files (text extraction via PyPDF2)",
            "usage": "Supplementary information to strengthen request evaluation"
        },
        "User Profiles (JSON)": {
            "description": "Pre-existing employee data including demographics, position, department, and financial status",
            "format": "JSON database (user_profiles.json)",
            "usage": "Cross-referencing and eligibility verification"
        },
        "NRIC Validation": {
            "description": "Singapore National Registration Identity Card number for unique identification",
            "format": "Alphanumeric string (S/T/F/G + 7 digits + letter)",
            "usage": "User authentication and profile linking"
        },
        "Assessment History": {
            "description": "Historical record of all processed requests and reviewer decisions",
            "format": "JSON Lines format (assessments_log.json)",
            "usage": "Audit trail, analytics, and pattern recognition"
        },
        "OpenAI GPT-4o-mini": {
            "description": "Large Language Model for natural language understanding and assessment generation",
            "format": "API calls to OpenAI",
            "usage": "Information extraction, summarization, and recommendation generation"
        }
    }
    
    for source, details in data_sources.items():
        with st.expander(f"📁 {source}"):
            st.write(f"*Description:* {details['description']}")
            st.write(f"*Format:* {details['format']}")
            st.write(f"*Usage:* {details['usage']}")
    
    st.divider()
    
    # Key Features
    st.subheader("✨ Key Features")
    
    features_col1, features_col2 = st.columns(2)
    
    with features_col1:
        st.markdown("""
        *🔐 Security & Validation*
        - NRIC format validation with regex patterns
        - Secure data storage and handling
        - Session-based user authentication
        - Data encryption in transit
        
        *🤖 AI-Powered Analysis*
        - Natural Language Processing for request understanding
        - Automated keyword detection and classification
        - Duration extraction using pattern matching
        - Context-aware recommendation generation
        
        *📄 Document Processing*
        - PDF text extraction and parsing
        - Multi-page document support
        - Content preview and verification
        - Combined analysis with text input
        
        *👤 User Profile Integration*
        - Automatic profile lookup by NRIC
        - Cross-reference with employee database
        - Eligibility verification
        - Historical context consideration
        """)
    
    with features_col2:
        st.markdown("""
        *📊 Assessment Generation*
        - Concise, actionable reports (max 200 words)
        - Clear GRANT/DENY recommendations
        - Duration recommendations with justification
        - Required approval authority determination
        
        *✍ Human Review Integration*
        - Mandatory reviewer opinion field
        - Free-form professional assessment
        - Decision override capability
        - Contextual notes and conditions
        
        *💾 Data Persistence*
        - JSON-based vector database storage
        - Complete audit trail for all requests
        - Request counter and analytics
        - Session state management
        
        *🎨 User Experience*
        - Intuitive web interface with Streamlit
        - Real-time validation and feedback
        - Expandable sections for detailed information
        - Clear visual indicators and status messages
        """)
    
    st.divider()
    
    # Technology Stack
    st.subheader("🛠 Technology Stack")
    
    tech_col1, tech_col2, tech_col3 = st.columns(3)
    
    with tech_col1:
        st.markdown("""
        *Frontend*
        - Streamlit 1.x
        - Python 3.8+
        - HTML/CSS
        """)
    
    with tech_col2:
        st.markdown("""
        *Backend*
        - OpenAI API (GPT-4o-mini)
        - PyPDF2 (Document Processing)
        - Python Standard Libraries
        """)
    
    with tech_col3:
        st.markdown("""
        *Data Storage*
        - JSON file-based database
        - JSON Lines format
        - Environment variables (.env)
        """)
    
    st.divider()
    
    # Project Information
    st.subheader("📌 Project Information")
    
    info_col1, info_col2 = st.columns(2)
    
    with info_col1:
        st.metric("Version", "2.0.0")
        st.metric("Last Updated", "November 2025")
        st.metric("Status", "Production")
    
    with info_col2:
        st.metric("AI Model", "GPT-4o-mini")
        st.metric("Framework", "Streamlit")
        st.metric("Language", "Python 3.8+")
    
    st.divider()
    
    st.info("""
    *Note:* This system is designed to assist human reviewers in making informed decisions. 
    All final approval decisions remain the responsibility of authorized personnel based on 
    organizational policies and regulations.
    """)
    

# Methodology Page
def methodology_page():
    st.title("📊 Methodology")
    
    st.markdown("""
    ## Comprehensive Assessment Methodology
    
    This page provides detailed insights into the data flows, implementation details, and process flows 
    for different user scenarios in the Automated Contribution Deferment Assessment System.
    """)
    
    st.divider()
    
    # System Architecture Overview
    st.subheader("🏗 System Architecture Overview")
    
    st.markdown("""
    The system follows a modular architecture with clear separation of concerns:
    
    1. *Presentation Layer*: Streamlit web interface for user interaction
    2. *Business Logic Layer*: Request processing, validation, and assessment generation
    3. *AI Integration Layer*: OpenAI API calls for NLP and analysis
    4. *Data Access Layer*: JSON-based storage and retrieval
    5. *Validation Layer*: NRIC validation, keyword detection, and format checking
    """)
    
    st.divider()
    
    # Detailed Data Flow
    st.subheader("🔄 Detailed Data Flow")
    
    tab1, tab2, tab3 = st.tabs(["📥 Input Processing", "🤖 AI Analysis", "💾 Data Storage"])
    
    with tab1:
        st.markdown("""
        ### Input Processing Flow
        
        *Step 1: User Input Collection*
        - User enters NRIC number (validated against Singapore NRIC format)
        - User provides request details in free-form text
        - Optional: User uploads supporting PDF documents
        
        *Step 2: Input Validation*
        - NRIC format validation using regex: ^[STFG]\\d{7}[A-Z]$
        - Check for non-empty request text
        - Validate PDF file format if uploaded
        
        *Step 3: Data Extraction*
        - Extract text from PDF using PyPDF2 library
        - Combine text input with PDF content
        - Normalize and clean input data
        
        *Step 4: Keyword Detection*
        - Scan for deferment-related keywords:
          - Primary: defer, deferment, postpone, extend
          - Financial: cannot pay, unable to pay, financial difficulty
          - Request types: exemption, waiver, suspension, pause
        - Classification: Deferment vs Non-Deferment request
        """)
        
        st.code("""
# Example Keywords
DEFERMENT_KEYWORDS = [
    "defer", "extend", "cannot pay", "exemption", 
    "postpone", "temporary reduction", "waiver", 
    "deferment", "delay", "suspend", "pause", 
    "unable to pay"
]
        """, language="python")
    
    with tab2:
        st.markdown("""
        ### AI Analysis Pipeline
        
        *Phase 1: Information Extraction*
        - *Model*: GPT-4o-mini (temperature: 0.3 for consistency)
        - *Task*: Extract structured information from unstructured text
        - *Output*: Duration, reason, start date, key details
        - *Token Limit*: Max 150 words for concise summary
        
        *Phase 2: Duration Detection*
        - *Local Processing*: Regex pattern matching for months/years
        - *Patterns Detected*:
          - Explicit months: "6 months", "for 3 months"
          - Years converted: "1 year" → 12 months
          - Implicit: "immediate" → 1 month
        - *Fallback*: AI extraction if local processing fails
        
        *Phase 3: Profile Cross-Reference*
        - Lookup user profile from JSON database using NRIC
        - Extract: Name, Age, Position, Department
        - Verify: Retirement savings status, Financial aid enrollment
        - Flag: Missing profiles for manual review
        
        *Phase 4: Assessment Generation*
        - *Model*: GPT-4o-mini (temperature: 0.5 for balanced output)
        - *Inputs*: Extracted info + User profile + Duration
        - *Prompt Engineering*: Structured prompt with clear guidelines
        - *Output*: Concise assessment (max 200 words) with:
          1. Request summary
          2. GRANT/DENY recommendation
          3. Recommended duration
          4. Main conditions
          5. Required approval authority
        """)
        
        st.info("""
        *AI Model Configuration:*
        - Model: gpt-4o-mini
        - Max Tokens: 1000
        - Temperature: 0.3 (extraction), 0.5 (assessment)
        - API: OpenAI Chat Completions
        """)
    
    with tab3:
        st.markdown("""
        ### Data Storage and Retrieval
        
        *Storage Format: JSON Lines*
        - Each record stored as separate JSON object on new line
        - File: assessments_log.json
        - Append-only for data integrity
        
        *Record Structure:*
        json
        {
          "timestamp": "2025-11-21T10:30:00",
          "assessment": {
            "is_deferment": true,
            "extracted_info": {...},
            "user_profile": {...},
            "summary": "...",
            "approval_authority": "..."
          },
          "reviewer_comments": "...",
          "nric": "S1234567D"
        }
        
        
        *User Profiles Storage:*
        - File: user_profiles.json
        - Format: Standard JSON object
        - Structure: NRIC as key, profile object as value
        
        *Request Counter:*
        - Real-time count from vector database
        - Displayed in sidebar
        - Updates on each submission
        """)
    
    st.divider()
    
    # Implementation Details
    st.subheader("⚙ Implementation Details")
    
    impl_tab1, impl_tab2, impl_tab3 = st.tabs(["🔐 Validation Logic", "📋 Approval Authority", "🔄 Session Management"])
    
    with impl_tab1:
        st.markdown("""
        ### NRIC Validation Algorithm
        
        *Validation Steps:*
        1. Check if input is not empty
        2. Trim whitespace and convert to uppercase
        3. Apply regex pattern: ^[STFG]\\d{7}[A-Z]$
        4. Verify:
           - First character: S (Singapore Citizen), T (TR born <2000), F (Foreigner), G (TR born ≥2000)
           - Next 7 characters: Digits (0-9)
           - Last character: Alphabet (A-Z)
        
        *Error Handling:*
        - Invalid format → Display error message
        - Empty input → No validation performed
        - Valid format → Display success indicator
        """)
        
        st.code("""
def validate_nric(nric):
    if not nric:
        return False
    nric = nric.strip().upper()
    pattern = r'^[STFG]\\d{7}[A-Z]$'
    return bool(re.match(pattern, nric))
        """, language="python")
    
    with impl_tab2:
        st.markdown("""
        ### Approval Authority Determination
        
        The system automatically determines the required approval authority based on requested duration:
        """)
        
        # Approval Authority Table
        authority_data = {
            "Duration": ["≤ 3 months", "4-6 months", "7-12 months", "> 12 months"],
            "Authority Level": [
                "Executive/Assistant Manager/Inspector+",
                "Manager+",
                "Assistant Director+",
                "Director+"
            ],
            "Typical Use Cases": [
                "Short-term financial difficulty, temporary medical leave",
                "Extended medical treatment, partial income reduction",
                "Long-term financial hardship, career transition",
                "Exceptional circumstances, permanent disability"
            ]
        }
        
        import pandas as pd
        df = pd.DataFrame(authority_data)
        st.table(df)
        
        st.code("""
def get_approval_authority(duration_months):
    if duration_months <= 3:
        return "Executive/Assistant Manager/Inspector and above"
    elif duration_months <= 6:
        return "Manager and above"
    elif duration_months <= 12:
        return "Assistant Director and above"
    else:
        return "Director and above"
        """, language="python")
    
    with impl_tab3:
        st.markdown("""
        ### Session State Management
        
        *Session Variables:*
        - messages: Chat history (if applicable)
        - logged_in: Authentication status
        - current_page: Active page navigation
        - form_key: Form reset counter
        - current_assessment: Active assessment data
        - reviewer_comments: Reviewer input text
        
        *Form Reset Mechanism:*
        1. Each input field has unique key: {field_name}_{form_key}
        2. On successful submission: form_key += 1
        3. New keys force Streamlit to create fresh widgets
        4. All fields reset to default empty state
        
        *Data Cleanup on Submit:*
        - Delete current_assessment from session state
        - Delete reviewer_comments from session state
        - Increment form_key for form reset
        - Trigger page rerun for clean slate
        """)
    
    st.divider()
    
    # Process Flow Diagrams
    st.subheader("📊 Process Flow Diagrams")
    
    flow_tab1, flow_tab2, flow_tab3 = st.tabs(["✅ Deferment Request", "❌ Non-Deferment Request", "🔄 Complete Workflow"])
    
    with flow_tab1:
        st.markdown("### Deferment Request Flow")
        
        st.code("""
┌─────────────────────────────────────────────────────────────────┐
│                    USER SUBMITS REQUEST                          │
│  • NRIC Number (validated)                                       │
│  • Request Details (text)                                        │
│  • Supporting Documents (optional PDF)                           │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                   INPUT VALIDATION                               │
│  ✓ NRIC format check (regex)                                    │
│  ✓ Non-empty request text                                       │
│  ✓ PDF extraction (if provided)                                 │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                  KEYWORD DETECTION                               │
│  • Scan for deferment keywords                                   │
│  • Result: IS DEFERMENT REQUEST ✓                               │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│              AI INFORMATION EXTRACTION                           │
│  • Duration extraction (regex + AI)                              │
│  • Reason identification                                         │
│  • Key details summary                                           │
│  Model: GPT-4o-mini (temp=0.3)                                   │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│              USER PROFILE LOOKUP                                 │
│  • Query user_profiles.json by NRIC                             │
│  • Extract: name, age, position, department                      │
│  • Check: retirement savings, financial aid                      │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│              AI ASSESSMENT GENERATION                            │
│  • Combine: extracted info + user profile                        │
│  • Generate: GRANT/DENY recommendation                           │
│  • Determine: approval authority level                           │
│  • Output: Concise report (max 200 words)                        │
│  Model: GPT-4o-mini (temp=0.5)                                   │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│              DISPLAY ASSESSMENT RESULTS                          │
│  ✓ Request Information (expandable)                              │
│  ✓ User Profile (expandable)                                     │
│  ✓ Assessment Summary (expandable)                               │
│  ✓ Approval Authority Required                                   │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│              REVIEWER ADDS OPINION                               │
│  • Free-form text area                                           │
│  • Professional assessment                                       │
│  • Conditions or notes                                           │
│  • REQUIRED before submission                                    │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│              SUBMIT ASSESSMENT                                   │
│  • Validate reviewer comments exist                              │
│  • Store in assessments_log.json                                │
│  • Timestamp record                                              │
│  • Update request counter                                        │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│              CLEANUP & RESET                                     │
│  • Clear current_assessment                                      │
│  • Clear reviewer_comments                                       │
│  • Increment form_key                                            │
│  • Display success message                                       │
│  • Reload page with fresh form                                   │
└─────────────────────────────────────────────────────────────────┘
        """, language="text")
    
    with flow_tab2:
        st.markdown("### Non-Deferment Request Flow")
        
        st.code("""
┌─────────────────────────────────────────────────────────────────┐
│                    USER SUBMITS REQUEST                          │
│  • NRIC Number (validated)                                       │
│  • Request Details (text)                                        │
│  • Supporting Documents (optional PDF)                           │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                   INPUT VALIDATION                               │
│  ✓ NRIC format check (regex)                                    │
│  ✓ Non-empty request text                                       │
│  ✓ PDF extraction (if provided)                                 │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                  KEYWORD DETECTION                               │
│  • Scan for deferment keywords                                   │
│  • Result: NOT A DEFERMENT REQUEST ✗                            │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│              DISPLAY WARNING MESSAGE                             │
│  ⚠ This does not appear to be a deferment request              │
│  • Show explanation message                                      │
│  • No deferment-related keywords detected                        │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│              REVIEWER ADDS OPINION                               │
│  • Free-form text area                                           │
│  • Document why request was flagged                              │
│  • Suggest proper channel if applicable                          │
│  • REQUIRED before submission                                    │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│              SUBMIT ASSESSMENT                                   │
│  • Validate reviewer comments exist                              │
│  • Store as non-deferment record                                │
│  • Flag: is_deferment = false                                    │
│  • Timestamp record                                              │
│  • Update request counter                                        │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│              CLEANUP & RESET                                     │
│  • Clear current_assessment                                      │
│  • Clear reviewer_comments                                       │
│  • Increment form_key                                            │
│  • Display success message                                       │
│  • Reload page with fresh form                                   │
└─────────────────────────────────────────────────────────────────┘
        """, language="text")
    
    with flow_tab3:
        st.markdown("### Complete System Workflow")
        
        st.code("""
                        ┌─────────────────────┐
                        │   USER LOGIN        │
                        │   (Session Start)   │
                        └──────────┬──────────┘
                                   │
                                   ▼
                        ┌─────────────────────┐
                        │  NAVIGATION MENU    │
                        │  • Home             │
                        │  • About Us         │
                        │  • Methodology      │
                        └──────────┬──────────┘
                                   │
                                   ▼
                ┌──────────────────┴──────────────────┐
                │                                      │
                ▼                                      ▼
    ┌───────────────────────┐          ┌───────────────────────┐
    │   INFORMATION PAGES   │          │    HOME PAGE          │
    │   • About Us          │          │    (Main Function)    │
    │   • Methodology       │          └───────────┬───────────┘
    └───────────────────────┘                      │
                                                    ▼
                                        ┌───────────────────────┐
                                        │  INPUT FORM           │
                                        │  • NRIC Input         │
                                        │  • Request Text       │
                                        │  • PDF Upload         │
                                        └───────────┬───────────┘
                                                    │
                                                    ▼
                                        ┌───────────────────────┐
                                        │  ANALYZE BUTTON       │
                                        │  (Trigger Processing) │
                                        └───────────┬───────────┘
                                                    │
                        ┌───────────────────────────┴───────────────────────────┐
                        │                                                       │
                        ▼                                                       ▼
            ┌──────────────────────┐                            ┌──────────────────────┐
            │   DEFERMENT PATH     │                            │  NON-DEFERMENT PATH  │
            │   (Keywords Found)   │                            │  (No Keywords)       │
            └──────────┬───────────┘                            └──────────┬───────────┘
                       │                                                    │
                       ▼                                                    ▼
            ┌──────────────────────┐                            ┌──────────────────────┐
            │  AI EXTRACTION       │                            │  WARNING MESSAGE     │
            │  • Duration          │                            │  • Not deferment     │
            │  • Reason            │                            │  • Explanation       │
            │  • Details           │                            └──────────┬───────────┘
            └──────────┬───────────┘                                       │
                       │                                                    │
                       ▼                                                    │
            ┌──────────────────────┐                                       │
            │  PROFILE LOOKUP      │                                       │
            │  • Match NRIC        │                                       │
            │  • Get user data     │                                       │
            └──────────┬───────────┘                                       │
                       │                                                    │
                       ▼                                                    │
            ┌──────────────────────┐                                       │
            │  AI ASSESSMENT       │                                       │
            │  • GRANT/DENY        │                                       │
            │  • Duration          │                                       │
            │  • Authority         │                                       │
            └──────────┬───────────┘                                       │
                       │                                                    │
                       └────────────────────┬───────────────────────────────┘
                                            │
                                            ▼
                                ┌───────────────────────┐
                                │  DISPLAY RESULTS      │
                                │  • Show assessment    │
                                │  • Show profile       │
                                │  • Show summary       │
                                └───────────┬───────────┘
                                            │
                                            ▼
                                ┌───────────────────────┐
                                │  REVIEWER OPINION     │
                                │  • Text area          │
                                │  • Required field     │
                                └───────────┬───────────┘
                                            │
                                            ▼
                                ┌───────────────────────┐
                                │  SUBMIT BUTTON        │
                                │  (Validation Check)   │
                                └───────────┬───────────┘
                                            │
                                            ▼
                                ┌───────────────────────┐
                                │  STORE IN DATABASE    │
                                │  • JSON Lines format  │
                                │  • Timestamp          │
                                │  • Complete record    │
                                └───────────┬───────────┘
                                            │
                                            ▼
                                ┌───────────────────────┐
                                │  CLEANUP & RESET      │
                                │  • Clear fields       │
                                │  • Increment form_key │
                                │  • Fresh form         │
                                └───────────────────────┘
        """, language="text")
    
    st.divider()
    
    # Error Handling
    st.subheader("⚠ Error Handling & Edge Cases")
    
    error_col1, error_col2 = st.columns(2)
    
    with error_col1:
        st.markdown("""
        *Input Validation Errors*
        - Invalid NRIC format → Display error message
        - Empty request text → Disable analyze button
        - PDF extraction failure → Graceful degradation
        - API key missing → Display configuration error
        
        *Data Processing Errors*
        - Missing user profile → Continue with warning
        - Duration extraction failure → Default to 0 months
        - Keyword detection ambiguous → Flag for review
        - JSON parsing error → Display specific error location
        """)
    
    with error_col2:
        st.markdown("""
        *API & External Errors*
        - OpenAI API timeout → Retry mechanism
        - Rate limiting → Display wait message
        - Network errors → Graceful error message
        - Invalid API response → Fallback to manual review
        
        *Storage Errors*
        - File write failure → Display error, don't clear form
        - Disk space full → Alert administrator
        - Permission denied → Check file permissions
        - Concurrent write conflicts → Append-only design prevents
        """)
    
    st.divider()
    
    st.info("""
    *Best Practices:*
    - Always validate input before processing
    - Maintain detailed audit trails
    - Implement graceful error handling
    - Preserve user data on errors
    - Provide clear feedback messages
    - Keep AI prompts consistent and tested
    - Regular backup of assessment logs
    - Monitor API usage and costs
    """)
    


# Main app logic
def main():
    if not st.session_state.logged_in:
        st.warning("You have been logged out.")
        st.stop()
    
    sidebar_navigation()
    
    # Route to appropriate page
    if st.session_state.current_page == "Home":
        home_page()
    elif st.session_state.current_page == "About Us":
        about_us_page()
    elif st.session_state.current_page == "Methodology":
        methodology_page()

if __name__ == "__main__":
    main()