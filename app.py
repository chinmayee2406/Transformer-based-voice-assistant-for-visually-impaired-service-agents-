from flask import Flask, request, jsonify, session
from flask_cors import CORS
from backend.language import detect_language, translate_text
from backend.semantic_search import get_best_answer
from backend.orchestrator import orchestrator_agent
import uuid
import time # For timestamps

app = Flask(__name__)
CORS(app)
app.secret_key = 'super_secret_key' # Replace with a strong, random key in production

# Simple in-memory storage for session context (for demonstration only)
# In a real application, use a proper session management system (e.g., Flask-Session, Redis, database)
session_contexts = {}

# Centralized storage for agent-customer chat messages
# Key: customer_id, Value: list of message dictionaries
# Each message dict: { 'sender': 'user'/'agent'/'bot', 'original_text': '', 'translated_text': '', 'lang': '', 'timestamp': '', 'read_by_agent': bool }
agent_customer_chats = {}

@app.route('/chat', methods=['POST'])
def chat():
    user_message = request.json.get('message')
    session_id = request.json.get('session_id')
    is_agent_chat = request.json.get('is_agent_chat', False)
    sender_type = request.json.get('sender_type') # 'customer' or 'agent'
    customer_id_for_agent = request.json.get('customer_id') # Only sent by agent

    if not session_id:
        session_id = str(uuid.uuid4())
    
    # Initialize context for this session if it doesn't exist
    if session_id not in session_contexts:
        session_contexts[session_id] = {
            'awaiting_customer_id': False,
            'awaiting_transaction_month': False,
            'user_query_for_orchestration': None,
            'customer_id': None, # This will store the actual customer_id once known
            'transaction_month': None,
            'is_connected_to_agent': False, # New flag to indicate agent connection
            'customer_original_lang': 'en', # Default, will be detected and updated
            'customer_name': 'Customer' # Default name
        }
    
    current_context = session_contexts[session_id]

    if not user_message:
        return jsonify({"error": "No message provided", "session_id": session_id}), 400

    current_time = time.strftime('%H:%M')

    if is_agent_chat:
        # This is a message exchanged between a customer and an agent
        # Determine the target customer ID for this chat
        target_customer_id = customer_id_for_agent if sender_type == 'agent' else current_context.get('customer_id')
        
        if not target_customer_id:
            return jsonify({"error": "Customer ID missing for agent chat", "session_id": session_id}), 400

        # Ensure a chat entry exists for this customer ID
        if target_customer_id not in agent_customer_chats:
            agent_customer_chats[target_customer_id] = []

        if sender_type == 'customer':
            # Customer sending message to agent
            detected_lang = detect_language(user_message)
            
            # Update the customer's original language in their session context
            # Find the session context associated with this customer_id
            found_session_id = None
            for sess_id, context in session_contexts.items():
                if context.get('customer_id') == target_customer_id:
                    found_session_id = sess_id
                    session_contexts[found_session_id]['customer_original_lang'] = detected_lang
                    break
            if not found_session_id:
                # Fallback: if session_id for this customer_id isn't found,
                # create a temporary context or use the current session_id
                # This handles cases where customer might refresh or session is lost
                session_contexts[session_id]['customer_id'] = target_customer_id
                session_contexts[session_id]['is_connected_to_agent'] = True
                session_contexts[session_id]['customer_original_lang'] = detected_lang


            translated_to_english = translate_text(user_message, source=detected_lang, target='en')
            
            message_obj = {
                'sender': 'user', # In the agent's view, this is 'user' (customer)
                'original_text': user_message,
                'translated_text': translated_to_english, # English for agent
                'lang': detected_lang,
                'timestamp': current_time,
                'read_by_agent': False # New message, not yet read by agent
            }
            agent_customer_chats[target_customer_id].append(message_obj)
            print(f"Customer message received for agent ({target_customer_id}): {user_message} (Original Lang: {detected_lang}) -> {translated_to_english} (English)")
            return jsonify({"status": "Message sent to agent", "session_id": session_id})

        elif sender_type == 'agent':
            # Agent sending message to customer
            customer_lang = 'en' # Default, assume agent types in English.
            # Find the customer's original language from session_contexts using target_customer_id
            for sess_id, context in session_contexts.items():
                if context.get('customer_id') == target_customer_id:
                    customer_lang = context.get('customer_original_lang', 'en')
                    break
            
            translated_to_customer_lang = translate_text(user_message, source='en', target=customer_lang)
            
            message_obj = {
                'sender': 'agent',
                'original_text': user_message,
                'translated_text': translated_to_customer_lang, # In customer's language
                'lang': 'en', # Agent's input language
                'timestamp': current_time
            }
            agent_customer_chats[target_customer_id].append(message_obj)
            print(f"Agent message received for customer ({target_customer_id}): {user_message} (Agent Input) -> {translated_to_customer_lang} (Customer Lang: {customer_lang})")
            return jsonify({"status": "Message sent to customer", "session_id": session_id})
        else:
            return jsonify({"error": "Invalid sender type for agent chat", "session_id": session_id}), 400

    else:
        # This is a customer-bot interaction
        lang = detect_language(user_message)
        current_context['customer_original_lang'] = lang # Store initial customer language
        
        # Existing bot logic
        try:
            if current_context['awaiting_customer_id']:
                current_context['customer_id'] = user_message.strip()
                current_context['awaiting_customer_id'] = False
                current_context['awaiting_transaction_month'] = True
                bot_response = "Thank you. Please enter the transaction month (e.g., 2024-05):"
            
            elif current_context['awaiting_transaction_month']:
                current_context['transaction_month'] = user_message.strip()
                current_context['awaiting_transaction_month'] = False
                
                original_query = current_context['user_query_for_orchestration']
                customer_id = current_context['customer_id']
                transaction_month = current_context['transaction_month']

                if original_query and customer_id and transaction_month:
                    print(f"[Orchestrator AI Agent Activated for session {session_id}]")
                    print(f"User Query: {original_query}, Customer ID: {customer_id}, Month: {transaction_month}")
                    orchestration_result = orchestrator_agent.orchestrate_transaction(
                        original_query, lang, customer_id, transaction_month
                    )
                    bot_response = orchestration_result
                    # Clear context after successful orchestration
                    session_contexts[session_id] = {
                        'awaiting_customer_id': False,
                        'awaiting_transaction_month': False,
                        'user_query_for_orchestration': None,
                        'customer_id': customer_id,
                        'transaction_month': None,
                        'is_connected_to_agent': False,
                        'customer_original_lang': lang,
                        'customer_name': current_context.get('customer_name', 'Customer')
                    }
                else:
                    bot_response = "I seem to have lost track of our conversation. Please start your query again."
                    # Clear context due to inconsistency
                    session_contexts[session_id] = {
                        'awaiting_customer_id': False,
                        'awaiting_transaction_month': False,
                        'user_query_for_orchestration': None,
                        'customer_id': None,
                        'transaction_month': None,
                        'is_connected_to_agent': False,
                        'customer_original_lang': lang,
                        'customer_name': current_context.get('customer_name', 'Customer')
                    }
            
            else: # Initial query or non-transactional query
                if orchestrator_agent.is_transactional(user_message, lang):
                    print(f"[Orchestrator AI Agent Detected Transactional Intent for session {session_id}]")
                    current_context['awaiting_customer_id'] = True
                    current_context['user_query_for_orchestration'] = user_message # Store original query
                    bot_response = "I can help with that! Please provide your Customer ID:"
                else:
                    # Fallback to semantic search for non-transactional queries
                    result = get_best_answer(user_message, source_lang=lang)
                    bot_response = result.get('translated_answer', "I'm sorry, I couldn't find an answer to that.")
                    # Ensure context is clean for non-transactional queries
                    session_contexts[session_id] = {
                        'awaiting_customer_id': False,
                        'awaiting_transaction_month': False,
                        'user_query_for_orchestration': None,
                        'customer_id': None,
                        'transaction_month': None,
                        'is_connected_to_agent': False,
                        'customer_original_lang': lang,
                        'customer_name': current_context.get('customer_name', 'Customer')
                    }

        except Exception as e:
            print(f"Error processing message with backend: {e}")
            bot_response = "I apologize, but I encountered an error. Please try again later."
            # Clear context on error to prevent being stuck
            session_contexts[session_id] = {
                'awaiting_customer_id': False,
                'awaiting_transaction_month': False,
                'user_query_for_orchestration': None,
                'customer_id': None,
                'transaction_month': None,
                'is_connected_to_agent': False,
                'customer_original_lang': lang,
                'customer_name': current_context.get('customer_name', 'Customer')
            }

        return jsonify({"response": bot_response, "session_id": session_id})

# New endpoint for agent to fetch messages for a specific customer
@app.route('/get_agent_messages', methods=['POST'])
def get_agent_messages():
    customer_id = request.json.get('customer_id')
    if not customer_id:
        return jsonify({"error": "Customer ID required"}), 400

    messages_for_agent = []
    if customer_id in agent_customer_chats:
        for msg in agent_customer_chats[customer_id]:
            if msg['sender'] == 'user': # These are customer messages
                messages_for_agent.append({
                    'sender': 'user',
                    'text': msg['translated_text'], # Agent gets English
                    'time': msg['timestamp']
                })
                msg['read_by_agent'] = True # Mark as read when agent fetches
            elif msg['sender'] == 'agent': # These are agent's own messages
                 messages_for_agent.append({
                    'sender': 'agent',
                    'text': msg['original_text'], # Agent sees their original English message
                    'time': msg['timestamp']
                })
            elif msg['sender'] == 'bot': # Include bot messages for agent's context
                messages_for_agent.append({
                    'sender': 'bot',
                    'text': msg['translated_text'], # Bot messages are already English
                    'time': msg['timestamp']
                })
    return jsonify({"messages": messages_for_agent})

# New endpoint for customer to fetch messages
@app.route('/get_customer_messages', methods=['POST'])
def get_customer_messages():
    session_id = request.json.get('session_id')
    if not session_id:
        return jsonify({"error": "Session ID required"}), 400

    customer_id = session_contexts[session_id].get('customer_id')
    if not customer_id:
        return jsonify({"error": "Customer ID not found for session"}), 400

    messages_for_customer = []
    if customer_id in agent_customer_chats:
        for msg in agent_customer_chats[customer_id]:
            if msg['sender'] == 'agent': # These are agent messages
                messages_for_customer.append({
                    'sender': 'bot', # Customer sees agent as 'bot' in their chat
                    'text': msg['translated_text'], # Customer gets their original language
                    'time': msg['timestamp']
                })
            elif msg['sender'] == 'user': # These are customer's own messages
                messages_for_customer.append({
                    'sender': 'user',
                    'text': msg['original_text'], # Customer sees their original message
                    'time': msg['timestamp']
                })
            elif msg['sender'] == 'bot': # Include bot messages for customer's context
                messages_for_customer.append({
                    'sender': 'bot',
                    'text': msg['original_text'], # Bot messages are already in customer's original language
                    'time': msg['timestamp']
                })
    return jsonify({"messages": messages_for_customer})

# New endpoint to initiate agent chat from customer side (sends initial history)
@app.route('/initiate_agent_chat', methods=['POST'])
def initiate_agent_chat():
    session_id = request.json.get('session_id')
    customer_name = request.json.get('customer_name')
    customer_id = request.json.get('customer_id')
    chat_history = request.json.get('chat_history') # List of {sender, text, time}

    if not session_id or not customer_name or not customer_id or not chat_history:
        return jsonify({"error": "Missing data for agent chat initiation"}), 400

    # Ensure session context exists and set connected to agent
    if session_id not in session_contexts:
        session_contexts[session_id] = {
            'awaiting_customer_id': False,
            'awaiting_transaction_month': False,
            'user_query_for_orchestration': None,
            'customer_id': customer_id, # Set customer_id here
            'transaction_month': None,
            'is_connected_to_agent': True,
            'customer_original_lang': 'en', # Will be updated by first customer message in history
            'customer_name': customer_name # Store customer name in session context
        }
    else:
        session_contexts[session_id]['is_connected_to_agent'] = True
        session_contexts[session_id]['customer_id'] = customer_id
        session_contexts[session_id]['customer_name'] = customer_name

    # Initialize chat history for this customer in agent_customer_chats
    if customer_id not in agent_customer_chats:
        agent_customer_chats[customer_id] = []

    # Process and add existing chat history to agent_customer_chats
    for msg in chat_history:
        if msg['sender'] == 'user':
            detected_lang = detect_language(msg['text'])
            session_contexts[session_id]['customer_original_lang'] = detected_lang # Update customer's language
            translated_to_english = translate_text(msg['text'], source=detected_lang, target='en')
            agent_customer_chats[customer_id].append({
                'sender': 'user',
                'original_text': msg['text'],
                'translated_text': translated_to_english,
                'lang': detected_lang,
                'timestamp': msg['time'],
                'read_by_agent': False # New message, not yet read by agent
            })
        elif msg['sender'] == 'bot':
            # For bot messages in history, we store them as is for agent's reference
            # No translation needed as they are already in English (from bot)
            agent_customer_chats[customer_id].append({
                'sender': 'bot', # Mark as bot message
                'original_text': msg['text'],
                'translated_text': msg['text'], # Bot messages are already English
                'lang': 'en',
                'timestamp': msg['time']
            })
        elif msg['sender'] == 'agent':
            # If agent messages are in the customer's history (e.g., from a previous agent chat)
            # We assume they were originally English when sent by agent
            agent_customer_chats[customer_id].append({
                'sender': 'agent',
                'original_text': msg['text'], # Agent's original message
                'translated_text': msg['text'], # Assume agent's message was in English
                'lang': 'en',
                'timestamp': msg['time']
            })


    print(f"Agent chat initiated for customer {customer_id}. History transferred.")
    return jsonify({"status": "Agent chat initiated", "session_id": session_id})

# New endpoint to get a summary of active customer chats for the agent dashboard sidebar
@app.route('/get_active_customer_chats', methods=['GET'])
def get_active_customer_chats():
    active_chats_summary = []
    for customer_id, messages in agent_customer_chats.items():
        customer_name = customer_id # Default to ID
        # Try to find the customer's name from session_contexts
        for sess_id, context in session_contexts.items():
            if context.get('customer_id') == customer_id:
                customer_name = context.get('customer_name', customer_id)
                break

        last_message = messages[-1] if messages else None
        last_message_text = "No messages"
        last_message_time = ""

        if last_message:
            if last_message['sender'] == 'user':
                last_message_text = last_message['translated_text'] # Agent sees customer's message in English
            elif last_message['sender'] == 'agent':
                last_message_text = last_message['original_text'] # Agent sees their own message in English
            elif last_message['sender'] == 'bot':
                last_message_text = last_message['translated_text'] # Agent sees bot's message in English
            last_message_time = last_message['timestamp']

        # Calculate unread count for agent (only user messages not yet read by agent)
        unread_count = sum(1 for msg in messages if msg['sender'] == 'user' and not msg.get('read_by_agent', False))

        active_chats_summary.append({
            'id': customer_id,
            'name': customer_name,
            'avatar': '👤', # Default avatar
            'lastMessage': last_message_text,
            'lastTime': last_message_time,
            'unread': unread_count
        })
    return jsonify({"chats": active_chats_summary})


if __name__ == '__main__':
    app.run(debug=True, port=5000)
