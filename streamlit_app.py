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
    
    ### 🔒 Enhanced NRIC Validation
    The system now includes comprehensive NRIC validation to ensure data integrity and prevent fraudulent requests:
    
    - *Format Validation*: Ensures NRIC follows the Singapore format (S/T/F/G + 7 digits + letter)
    - *Database Verification*: Checks if NRIC exists in the authorized user profiles database
    - *Document Matching*: Validates that NRIC in submitted documents matches the form input
    - *Automatic Rejection*: Requests failing any validation check are automatically denied
    """)
    
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
    
    This page provides detailed insights into the enhanced NRIC validation process, data flows, 
    and implementation details for the Automated Contribution Deferment Assessment System.
    """)
    
    st.divider()
    
    # NRIC Validation Process
    st.subheader("🔒 NRIC Validation Process")
    
    st.markdown("""
    The system implements a three-tier NRIC validation process to ensure data integrity and prevent unauthorized access:
    
    1. *Format Validation*: Ensures NRIC follows Singapore format (S/T/F/G + 7 digits + letter)
    2. *Database Verification*: Checks if NRIC exists in the authorized user profiles database
    3. *Document Matching*: Validates that NRIC in submitted documents matches the form input
    """)
    
    st.divider()
    
    st.info("""
    *Security Notice:* The three-tier NRIC validation process significantly enhances system security by:
    1. Preventing typos and format errors
    2. Blocking unauthorized access attempts
    3. Detecting potential identity fraud
    4. Ensuring document authenticity
    
    All rejected requests are logged for security monitoring and audit purposes.
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