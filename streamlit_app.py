import streamlit as st
from utility import check_password
import json
import os
from datetime import datetime
from openai import OpenAI
import PyPDF2
import io
import re
import numpy as np

# Do not continue if check_password is not True.  
if not check_password():  
    st.stop()

# File paths
USER_PROFILES_FILE = "user_profiles.json"
ASSESSMENTS_LOG_FILE = "assessments_log.json"



# Deferment keywords
DEFERMENT_KEYWORDS = [
    "defer", "extend", "cannot pay", "exemption", "postpone",
    "temporary reduction", "waiver", "deferment", "delay",
    "suspend", "pause", "unable to pay"
]

# Initialize OpenAI client
@st.cache_resource
def get_openai_client():
    """Initialize and return OpenAI client"""
    openai_api_key = os.getenv('OPENAI_API_KEY')
    if not openai_api_key:
        st.error("⚠ OpenAI API key not found. Please set OPENAI_API_KEY in environment variables or Streamlit secrets.")
        st.stop()
    return OpenAI(api_key=openai_api_key)

# Initialize data files
def initialize_data_files():
    if not os.path.exists(USER_PROFILES_FILE):
        with open(USER_PROFILES_FILE, 'w') as f:
            json.dump(SAMPLE_PROFILES, f, indent=2)
    
    if not os.path.exists(ASSESSMENTS_LOG_FILE):
        with open(ASSESSMENTS_LOG_FILE, 'w') as f:
            json.dump([], f, indent=2)

# Load data
def load_user_profiles():
    try:
        with open(USER_PROFILES_FILE, 'r') as f:
            return json.load(f)
    except:
        return SAMPLE_PROFILES

def load_assessments():
    try:
        with open(ASSESSMENTS_LOG_FILE, 'r') as f:
            return json.load(f)
    except:
        return []

def save_assessment(assessment):
    assessments = load_assessments()
    assessments.append(assessment)
    with open(ASSESSMENTS_LOG_FILE, 'w') as f:
        json.dump(assessments, f, indent=2)

# Extract text from PDF
def extract_pdf_text(pdf_file):
    try:
        pdf_reader = PyPDF2.PdfReader(io.BytesIO(pdf_file.read()))
        text = ""
        for page in pdf_reader.pages:
            text += page.extract_text()
        return text
    except Exception as e:
        return f"Error extracting PDF: {str(e)}"

# OpenAI GPT analysis
def analyze_with_gpt(prompt, temperature=0.3):
    try:
        client = get_openai_client()
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"Error: {str(e)}"

# Check if request is deferment type
def check_deferment_request(reason):
    reason_lower = reason.lower()
    is_deferment = any(keyword in reason_lower for keyword in DEFERMENT_KEYWORDS)
    return is_deferment

# Extract duration from text
def extract_duration(reason, pdf_text):
    prompt = f"""
    Analyze the following deferment reason and supporting document to extract:
    1. Duration requested (in months)
    2. Specific period/dates if mentioned
    
    Deferment Reason: {reason}
    
    Supporting Document: {pdf_text[:2000]}
    
    Return in format:
    Duration: [X months]
    Period: [dates or "Not specified"]
    """
    
    response = analyze_with_gpt(prompt)
    return response

# Check eligibility criteria
def check_eligibility(user_profile, reason, pdf_text):
    eligibility_results = {
        "age_retirement": False,
        "income_loss": False,
        "financial_assistance": False,
        "details": []
    }
    
    reason_lower = reason.lower()
    
    # Criterion 1: Age & Retirement Savings
    age_related_keywords = ["retirement", "retire", "age", "senior", "elderly"]
    check_age_criterion = any(keyword in reason_lower for keyword in age_related_keywords)
    
    if user_profile:
        age = user_profile.get("age", 0)
        retirement_met = user_profile.get("retirement_savings_met", False)
        
        if check_age_criterion or (age >= 55 and retirement_met):
            if age >= 55 and retirement_met:
                eligibility_results["age_retirement"] = True
                eligibility_results["details"].append(
                    f"✓ Age & Retirement: User is {age} years old and has met retirement savings requirements"
                )
            else:
                eligibility_results["details"].append(
                    f"✗ Age & Retirement: User does not meet criteria (Age: {age}, Savings Met: {retirement_met})"
                )
    
    # Criterion 2: Income Loss
    income_loss_reasons = ["incarceration", "incarcerated", "prison", "jail", "detained","court",
                          "hospitalisation", "hospitalization", "hospital", "admitted","medical","health",
                          "medical certificate", "mc", "medical leave", "hl", "sick leave", "ill", "illness"]
    
    if any(keyword in reason_lower for keyword in income_loss_reasons):
        eligibility_results["details"].append(f"ℹ Income Loss category detected in reason: {reason[:100]}...")
        
        if pdf_text and len(pdf_text) > 50:
            prompt = f"""
            Analyze if this document supports income loss due to incarceration, hospitalization, or medical certificate (minimum 1 month duration).
            
            Document Content: {pdf_text[:2000]}
            
            Reason Stated: {reason}
            
            Does the document provide valid proof of:
            1. Incarceration (legal detention preventing work), OR
            2. Hospitalization Leave (extended medical treatment), OR
            3. Medical Certificate (minimum 1 month duration)?
            
            Answer with: Yes or No, followed by a brief explanation of what was found in the document.
            """
            
            verification = analyze_with_gpt(prompt)
            
            if "yes" in verification.lower():
                eligibility_results["income_loss"] = True
                eligibility_results["details"].append(
                    f"✓ Income Loss: Valid supporting document provided - {verification[:200]}"
                )
            else:
                eligibility_results["details"].append(
                    f"✗ Income Loss: Supporting document does not adequately verify claim - {verification[:200]}"
                )
        else:
            eligibility_results["details"].append(
                "✗ Income Loss: Required supporting document not submitted or document is too short"
            )
    
    # Criterion 3: Financial Assistance
    financial_keywords = ["financial aid", "financial assistance", "aid", "assistance", 
                         "welfare", "subsidy", "support scheme", "hardship"]
    
    if any(keyword in reason_lower for keyword in financial_keywords):
        eligibility_results["details"].append(f"ℹ Financial Assistance category detected in reason")
        
        if user_profile and user_profile.get("receiving_financial_aid", False):
            eligibility_results["financial_assistance"] = True
            eligibility_results["details"].append(
                "✓ Financial Assistance: User is currently receiving financial aid"
            )
        else:
            eligibility_results["details"].append(
                "✗ Financial Assistance: User is not currently receiving financial aid"
            )
    
    if not eligibility_results["details"]:
        eligibility_results["details"].append(
            "⚠ No eligibility criteria detected in the deferment reason. Please ensure reason mentions: "
            "retirement/age (55+), incarceration/hospitalization/medical leave, or financial assistance."
        )
    
    return eligibility_results

# Determine approval authority
def determine_approval_authority(duration_months):
    if duration_months <= 3:
        return "Executive/Assistant Manager/Inspector and above"
    elif duration_months <= 6:
        return "Manager and above"
    elif duration_months <= 12:
        return "Assistant Director and above"
    else:
        return "Director and above"

# Initialize session state
if 'page' not in st.session_state:
    st.session_state.page = 'Home'
if 'form_submitted' not in st.session_state:
    st.session_state.form_submitted = False

# Initialize data files
initialize_data_files()

# Right Sidebar
with st.sidebar:
    st.title("🧭 Navigation")
    st.markdown("---")
    
    if st.button("🏠 Home", use_container_width=True, type="primary" if st.session_state.page == 'Home' else "secondary"):
        st.session_state.page = 'Home'
        st.rerun()
    
    if st.button("ℹ About Us", use_container_width=True, type="primary" if st.session_state.page == 'About Us' else "secondary"):
        st.session_state.page = 'About Us'
        st.rerun()
    
    if st.button("🔬 Methodology", use_container_width=True, type="primary" if st.session_state.page == 'Methodology' else "secondary"):
        st.session_state.page = 'Methodology'
        st.rerun()
    
    st.markdown("---")
    
    # Logout button
    if st.button("🚪 Logout", type="secondary", use_container_width=True):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()

# Get current page
page = st.session_state.page

# HOME PAGE
if page == "Home":
    st.title("📋 Automated Contribution Deferment Assessment System")
    st.markdown("---")
    
    # Reset form submitted flag when returning to this page
    if st.session_state.form_submitted:
        st.session_state.form_submitted = False
        st.rerun()
    
    # Input Form
    col1, col2 = st.columns([2, 1])
    
    with col1:
        st.subheader("Request Form")
        
        # Create form with unique keys
        with st.form(key="assessment_form", clear_on_submit=True):
            nric = st.text_input(
                "NRIC Number", 
                placeholder="e.g., S1234567D"
            )
            reason = st.text_area(
                "Deferment Reason", 
                placeholder="Please state your reason for deferment...", 
                height=100
            )
            uploaded_file = st.file_uploader("Upload Supporting Document (PDF)", type=['pdf'])
            
            assess_btn = st.form_submit_button("🔍 Assess Request", type="primary", use_container_width=True)
        
        if assess_btn:
            if nric and reason:
                with st.spinner("Processing your request..."):
                    user_profiles = load_user_profiles()
                    user_profile = user_profiles.get(nric.upper())
                    
                    pdf_text = ""
                    if uploaded_file:
                        pdf_text = extract_pdf_text(uploaded_file)
                    
                    st.markdown("---")
                    st.header("Assessment Results")
                    
                    # 1. User Profile Section
                    st.subheader("User Profile")
                    if user_profile:
                        st.success("✓ User found in system database")
                        
                        profile_col1, profile_col2, profile_col3 = st.columns(3)
                        
                        with profile_col1:
                            st.markdown("##### 👤 Personal Information")
                            name = user_profile.get('name', 'N/A')
                            age = user_profile.get('age', 'N/A')
                            join_date = user_profile.get('join_date', 'N/A')
                            st.markdown(f"*Name:* {name}")
                            st.markdown(f"*NRIC:* {nric.upper()}")
                            st.markdown(f"*Age:* {age} years")
                            st.markdown(f"*Join Date:* {join_date}")
                        
                        with profile_col2:
                            st.markdown("##### 💰 Financial Status")
                            retirement_status = "✅ Met" if user_profile.get('retirement_savings_met', False) else "❌ Not Met"
                            financial_aid_status = "✅ Receiving" if user_profile.get('receiving_financial_aid', False) else "❌ Not Receiving"
                            st.markdown(f"*Retirement Savings:* {retirement_status}")
                            st.markdown(f"*Financial Aid Status:* {financial_aid_status}")
                        
                        with profile_col3:
                            st.markdown("##### 📧 Contact")
                            email = user_profile.get('email', 'N/A')
                            st.markdown(f"*Email:* {email}")
                    else:
                        st.error("✗ User not found in system database - REQUEST DENIED")
                        st.stop()
                    
                    # 2. Request Type Section
                    st.subheader("Request Type")
                    is_deferment = check_deferment_request(reason)
                    
                    if is_deferment:
                        st.success("✓ Deferment Request Detected")
                        detected_keywords = [kw for kw in DEFERMENT_KEYWORDS if kw in reason.lower()]
                        st.info(f"*Keywords found:* {', '.join(detected_keywords)}")
                    else:
                        st.warning("⚠ Non-Deferment Request - No further assessment required")
                        st.info("The system did not detect deferment-related keywords in your request.")
                        st.stop()
                    
                    # 3. Duration Section
                    st.subheader("Duration Analysis")
                    duration_analysis = extract_duration(reason, pdf_text)
                    st.info(duration_analysis)
                    
                    duration_months = 3  # Default
                    try:
                        months_match = re.search(r'(\d+)\s*month', duration_analysis.lower())
                        if months_match:
                            duration_months = int(months_match.group(1))
                    except:
                        pass
                    
                    # 4. Eligibility Criteria Section
                    st.subheader("Eligibility Criteria")
                    eligibility = check_eligibility(user_profile, reason, pdf_text)
                    
                    for detail in eligibility["details"]:
                        if "✓" in detail:
                            st.success(detail)
                        elif "✗" in detail:
                            st.error(detail)
                        else:
                            st.info(detail)
                    
                    criteria_met = (eligibility["age_retirement"] or 
                                   eligibility["income_loss"] or 
                                   eligibility["financial_assistance"])
                    
                    # 5. Approval Authority Section
                    st.subheader("Approval Authority")
                    authority = determine_approval_authority(duration_months)
                    st.info(f"*Required Approval Level:* {authority}")
                    st.caption(f"Based on deferment duration of {duration_months} months")
                    
                    # 6. Summary Section
                    st.subheader("Assessment Summary")
                    
                    decision = "Approved" if criteria_met else "Denied"
                    
                    if decision == "Approved":
                        st.success(f"### ✅ Request Status: {decision}")
                    else:
                        st.error(f"### ❌ Request Status: {decision}")
                    
                    summary_bullets = [
                        f"*User:* {user_profile['name']} ({nric.upper()})",
                        f"*Request Type:* Deferment",
                        f"*Duration:* {duration_months} months",
                        f"*Approval Authority Required:* {authority}",
                        "",
                        "*Eligibility Assessment:*"
                    ]
                    
                    for detail in eligibility["details"]:
                        summary_bullets.append(f"  - {detail}")
                    
                    if criteria_met:
                        summary_bullets.append("")
                        summary_bullets.append("*Recommendation:* Request meets eligibility criteria and may proceed for approval.")
                    else:
                        summary_bullets.append("")
                        summary_bullets.append("*Reason for Denial:* User does not meet any of the required eligibility criteria.")
                    
                    for bullet in summary_bullets:
                        st.markdown(bullet)
                    
                    # 7. Approver Review
                    st.subheader("Approver Review")
                    
                    with st.form(key="submission_form"):
                        approver_opinion = st.text_area("Approver Opinion/Comments", height=100)
                        submit_btn = st.form_submit_button("📝 Submit Assessment", type="primary", use_container_width=True)
                        
                        if submit_btn:
                            assessment_record = {
                                "timestamp": datetime.now().isoformat(),
                                "nric": nric.upper(),
                                "user_name": user_profile['name'],
                                "reason": reason,
                                "decision": decision,
                                "duration_months": duration_months,
                                "approval_authority": authority,
                                "eligibility": eligibility,
                                "approver_opinion": approver_opinion,
                                "summary": summary_bullets
                            }
                            
                            save_assessment(assessment_record)
                            st.success("✅ Assessment submitted and saved successfully!")
                            st.balloons()
                            
                            # Set flag to clear form on next render
                            st.session_state.form_submitted = True
                            st.rerun()
            else:
                st.error("⚠ Please fill in all required fields (NRIC and Reason)")

# ABOUT US PAGE
elif page == "About Us":
    st.title("ℹ About Us")
    st.markdown("---")
    
    st.header("Project Overview")
    st.markdown("""
    The *Automated Contribution Deferment Assessment System* is designed to streamline 
    and automate the evaluation of deferment requests for contribution payments.
    """)
    
    st.subheader("🎯 Project Objectives")
    st.markdown("""
    - *Automate Assessment:* Reduce manual processing time and human error
    - *Ensure Consistency:* Apply standardized eligibility criteria across all requests
    - *Improve Transparency:* Provide clear reasoning for approval/denial decisions
    - *Enhance Efficiency:* Enable quick turnaround for legitimate deferment requests
    - *Maintain Compliance:* Ensure all assessments follow regulatory requirements
    - *Vector Database Storage:* Store assessments with AI embeddings for intelligent search
    """)
    
    st.subheader("📊 Data Sources")
    st.markdown("""
    1. *User Profile Database:* Contains employee information including:
       - Personal details (Name, Age, NRIC)
       - Retirement savings status
       - Financial aid status
       - Employment history
    
    2. *Supporting Documents:* PDF submissions including:
       - Medical certificates
       - Hospitalization records
       - Incarceration documentation
       - Financial assistance proof
    
    3. *Vector Database (assessments_log.json):* Stores assessments with embeddings for:
       - Historical record of all deferment requests and decisions
       - AI-powered similarity search
       - Pattern recognition and insights
    """)
    
    st.subheader("✨ Key Features")
    st.markdown("""
    - *AI-Powered Analysis:* Uses GPT-4-mini for intelligent document analysis
    - *Vector Embeddings:* Each assessment is stored with semantic embeddings
    - *Similarity Search:* Find similar past cases using AI vector search
    - *Multi-Criteria Evaluation:* Assesses age, retirement savings, income loss, and financial assistance
    - *Duration Extraction:* Automatically identifies requested deferment periods
    - *Authority Determination:* Assigns appropriate approval level based on duration
    - *Comprehensive Logging:* Maintains complete audit trail with searchable vectors
    """)

# METHODOLOGY PAGE
elif page == "Methodology":
    st.title("🔬 Methodology")
    st.markdown("---")
    
    st.header("System Process Flowchart")
    
    # Create Mermaid flowchart
    mermaid_code = """
    flowchart TD
        Start([Start: User Submits Request]) --> Input[/"Input Stage<br/>- NRIC Number<br/>- Deferment Reason<br/>- Supporting Document PDF"/]
        
        Input --> Validate{Data<br/>Validation<br/>OK?}
        Validate -->|No| Error1[/"Error: Missing Required Fields"/]
        Error1 --> End1([End])
        
        Validate -->|Yes| UserCheck[/"User Verification<br/>Query Database by NRIC"/]
        
        UserCheck --> UserExists{User<br/>Found?}
        UserExists -->|No| Deny1[/"REQUEST DENIED<br/>User Not in Database"/]
        Deny1 --> ApproverReview1[/"Approver Review<br/>& Comments"/]
        ApproverReview1 --> SaveVector1[/"Save to Vector DB"/]
        SaveVector1 --> End2([End])
        
        UserExists -->|Yes| DisplayProfile[/"Display User Profile<br/>- Personal Info<br/>- Financial Status<br/>- Contact Details"/]
        
        DisplayProfile --> RequestType[/"Request Classification<br/>Check for Deferment Keywords"/]
        
        RequestType --> IsDeferment{Deferment<br/>Request?}
        IsDeferment -->|No| NonDeferment[/"Non-Deferment Request<br/>No Further Assessment"/]
        NonDeferment --> ApproverReview2[/"Approver Review<br/>& Comments"/]
        ApproverReview2 --> SaveVector2[/"Save to Vector DB"/]
        SaveVector2 --> End3([End])
        
        IsDeferment -->|Yes| Duration[/"Duration Analysis<br/>AI Extracts Duration<br/>from Reason & PDF"/]
        
        Duration --> Eligibility[/"Eligibility Assessment<br/>Check 3 Criteria"/]
        
        Eligibility --> Criterion1{Age &<br/>Retirement<br/>Savings?}
        Eligibility --> Criterion2{Income Loss<br/>Incarceration/<br/>Hospitalization/<br/>Medical Cert?}
        Eligibility --> Criterion3{Financial<br/>Assistance<br/>Recipient?}
        
        Criterion1 -->|Yes| Eligible[Eligible]
        Criterion2 -->|Yes| Eligible
        Criterion3 -->|Yes| Eligible
        
        Criterion1 -->|No| CheckOthers1{ }
        Criterion2 -->|No| CheckOthers1
        Criterion3 -->|No| CheckOthers1
        
        CheckOthers1 -->|All No| NotEligible[Not Eligible]
        
        Eligible --> Authority[/"Approval Authority<br/>Determination<br/>Based on Duration"/]
        NotEligible --> Authority
        
        Authority --> Summary[/"Generate Summary<br/>- Assessment Results<br/>- Eligibility Details<br/>- Recommendation"/]
        
        Summary --> Decision{Eligible?}
        Decision -->|Yes| Approved[/"✅ REQUEST APPROVED<br/>Meets Eligibility Criteria"/]
        Decision -->|No| Denied[/"❌ REQUEST DENIED<br/>Does Not Meet Criteria"/]
        
        Approved --> ApproverReview3[/"Approver Review<br/>& Final Comments"/]
        Denied --> ApproverReview3
        
        ApproverReview3 --> GenerateEmbedding[/"Generate AI Embedding<br/>for Vector Search"/]
        
        GenerateEmbedding --> SaveVector3[/"Save to Vector DB<br/>assessments_log.json<br/>with Embedding"/]
        
        SaveVector3 --> Success[/"✅ Assessment Saved<br/>Form Cleared"/]
        
        Success --> End4([End])
        
        style Start fill:#e1f5e1
        style End1 fill:#ffe1e1
        style End2 fill:#ffe1e1
        style End3 fill:#fff4e1
        style End4 fill:#e1f5e1
        style Deny1 fill:#ffcccc
        style Denied fill:#ffcccc
        style Approved fill:#ccffcc
        style Success fill:#ccffcc
        style Eligible fill:#ccffcc
        style NotEligible fill:#ffcccc
        style SaveVector1 fill:#cce5ff
        style SaveVector2 fill:#cce5ff
        style SaveVector3 fill:#cce5ff
        style GenerateEmbedding fill:#e1d5ff
    """
    
    st.components.v1.html(f"""
    <script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
    <script>
        mermaid.initialize({{ startOnLoad: true, theme: 'default', flowchart: {{ useMaxWidth: true, htmlLabels: true }} }});
    </script>
    <div class="mermaid">
        {mermaid_code}
    </div>
    """, height=2400, scrolling=True)
    
    st.markdown("---")
    
    st.header("Process Details")
    
    with st.expander("📥 1. Input & Validation Stage"):
        st.markdown("""
        *User Inputs:*
        - NRIC number
        - Deferment reason (free text)
        - Supporting document (PDF upload)
        
        *Data Validation:*
        - NRIC format verification
        - File type validation (PDF only)
        - Mandatory field checks
        """)
    
    with st.expander("👤 2. User Verification"):
        st.markdown("""
        *Process:*
        1. System queries user profile database using NRIC
        2. Retrieves complete user profile if exists
        3. Validates profile completeness
        
        *Decision Point:*
        - ✅ User Found → Proceed to next stage
        - ❌ User Not Found → *DENY REQUEST* (Exit process)
        """)
    
    with st.expander("🔍 3. Request Classification"):
        st.markdown("""
        *Deferment Detection:*
        - System analyzes deferment reason text
        - Checks for presence of deferment keywords
        
        *Classification Result:*
        - ✅ Deferment Request → Continue assessment
        - ⚠ Non-Deferment Request → *NO FURTHER ASSESSMENT* (Exit process)
        """)
    
    with st.expander("⏱ 4. Duration Analysis"):
        st.markdown("""
        *AI-Powered Extraction:*
        - GPT-4-mini analyzes both deferment reason and PDF content
        - Identifies duration in months
        - Extracts specific dates/periods if mentioned
        """)
    
    with st.expander("✅ 5. Eligibility Assessment"):
        st.markdown("""
        *Three Criteria Evaluation:*
        
        1. *Age & Retirement Savings*
           - User age ≥ 55 years
           - Retirement savings requirements met
        
        2. *Income Loss*
           - Incarceration (legal detention)
           - Hospitalization (extended medical treatment)
           - Medical Certificate (minimum 1 month duration)
           - Must have valid supporting document
        
        3. *Financial Assistance*
           - Currently receiving financial aid
        
        *Decision:*
        - At least ONE criterion must be met for approval
        - All criteria failed → *DENY REQUEST*
        """)
    
    with st.expander("👔 6. Approval Authority Determination"):
        st.markdown("""
        *Authority Matrix:*
        
        | Duration | Required Authority |
        |----------|-------------------|
        | ≤ 3 months | Executive/Assistant Manager/Inspector and above |
        | 4-6 months | Manager and above |
        | 7-12 months | Assistant Director and above |
        | > 12 months | Director and above |
        """)
    
    with st.expander("💾 7. Vector Database Storage"):
        st.markdown("""
        *Process:*
        1. Generate comprehensive summary of assessment
        2. Approver reviews and adds comments
        3. Create embedding text from assessment details
        4. Generate AI vector embedding using OpenAI
        5. Save to assessments_log.json with:
           - Assessment record
           - Vector embedding
           - Unique vector ID
           - Metadata
        6. Form automatically clears for next request
        
        *Benefits:*
        - Enables AI-powered similarity search
        - Find similar past cases instantly
        - Pattern recognition and insights
        - Complete audit trail
        """)