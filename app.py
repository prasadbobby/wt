from flask import Flask, request, jsonify
from flask_cors import CORS
import logging
import os
from datetime import datetime
import json
import re
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
import requests
from pymongo import MongoClient
from bson import ObjectId

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# Configuration
class Config:
    TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID')
    TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN')
    WHATSAPP_FROM_NUMBER = os.environ.get('WHATSAPP_FROM_NUMBER', 'whatsapp:+17623566543')
    MONGO_URI = os.environ.get('MONGO_URI', 'mongodb+srv://bobby:bobby@cluster0.nvavp.mongodb.net/villagestay')
    GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

# Initialize Twilio
twilio_client = Client(Config.TWILIO_ACCOUNT_SID, Config.TWILIO_AUTH_TOKEN)

# Initialize MongoDB
mongo_client = MongoClient(Config.MONGO_URI)
db = mongo_client.villagestay

class WhatsAppBot:
    def __init__(self):
        self.conversation_states = {}
    
    def process_message(self, from_number, message_body):
        """Process incoming WhatsApp message"""
        try:
            logger.info(f"Processing message from {from_number}: {message_body}")
            
            # Clean phone number
            clean_number = self.clean_phone_number(from_number)
            
            # Get conversation state
            conversation = self.get_conversation_state(clean_number)
            
            # Process based on current state
            response = self.handle_message_by_state(clean_number, message_body, conversation)
            
            return response
            
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            return "Sorry, I encountered an error. Please try again or type 'help'."
    
    def clean_phone_number(self, phone):
        """Clean phone number format"""
        return phone.replace('whatsapp:', '').strip()
    
    def get_conversation_state(self, phone_number):
        """Get or create conversation state"""
        conversation = db.whatsapp_conversations.find_one({
            "phone_number": phone_number,
            "status": "active"
        })
        
        if not conversation:
            conversation = {
                "phone_number": phone_number,
                "state": "greeting",
                "data": {},
                "created_at": datetime.utcnow(),
                "last_activity": datetime.utcnow(),
                "status": "active"
            }
            result = db.whatsapp_conversations.insert_one(conversation)
            conversation["_id"] = result.inserted_id
        
        return conversation
    
    def update_conversation_state(self, conversation_id, new_state, data=None):
        """Update conversation state"""
        update_data = {
            "state": new_state,
            "last_activity": datetime.utcnow()
        }
        if data:
            update_data["data"] = data
        
        db.whatsapp_conversations.update_one(
            {"_id": conversation_id},
            {"$set": update_data}
        )
    
    def handle_message_by_state(self, phone_number, message, conversation):
        """Route message based on conversation state"""
        state = conversation.get("state", "greeting")
        
        if state == "greeting":
            return self.handle_greeting(conversation)
        elif state == "searching":
            return self.handle_search(message, conversation)
        elif state == "booking":
            return self.handle_booking(message, conversation)
        elif state == "details":
            return self.handle_details(message, conversation)
        else:
            return self.handle_greeting(conversation)
    
    def handle_greeting(self, conversation):
        """Handle initial greeting"""
        greeting_msg = """🏡 *Welcome to VillageStay!* 🌾

I can help you find and book authentic rural stays across India!

What would you like to do?

1️⃣ *Search stays* - "Find stays in Goa"
2️⃣ *Popular destinations* - See trending places
3️⃣ *Help* - Get assistance

Just tell me where you'd like to stay! 🗺️"""

        self.update_conversation_state(conversation["_id"], "searching")
        return greeting_msg
    
    def handle_search(self, message, conversation):
        """Handle search queries"""
        try:
            # Simple search logic
            search_results = self.search_listings(message)
            
            if not search_results:
                return """🔍 No stays found. Try:
- "Goa homestays"
- "Kerala farmstays" 
- "Rajasthan village stays"

Or type 'popular' for trending destinations."""
            
            # Format results
            response = f"🏠 *Found {len(search_results)} stays:*\n\n"
            
            for i, listing in enumerate(search_results[:3], 1):
                response += f"*{i}. {listing['title']}*\n"
                response += f"📍 {listing['location']}\n"
                response += f"💰 ₹{listing['price']}/night\n"
                response += f"⭐ {listing.get('rating', 'New')}\n\n"
            
            response += "Reply with number (1-3) to book or search again! 📱"
            
            # Store results
            data = conversation.get("data", {})
            data["search_results"] = search_results
            self.update_conversation_state(conversation["_id"], "booking", data)
            
            return response
            
        except Exception as e:
            logger.error(f"Search error: {e}")
            return "Search failed. Please try again with location name."
    
    def handle_booking(self, message, conversation):
        """Handle booking selection"""
        try:
            data = conversation.get("data", {})
            search_results = data.get("search_results", [])
            
            # Check if user selected a number
            if message.strip() in ['1', '2', '3']:
                selection = int(message.strip()) - 1
                if 0 <= selection < len(search_results):
                    selected_listing = search_results[selection]
                    data["selected_listing"] = selected_listing
                    
                    booking_msg = f"""✨ *{selected_listing['title']}*

📍 {selected_listing['location']}
💰 ₹{selected_listing['price']}/night
🏠 {selected_listing.get('type', 'Homestay')}

*Ready to book?*

Please provide:
📅 Check-in date (e.g., "Dec 25")
📅 Check-out date (e.g., "Dec 28") 
👥 Number of guests (e.g., "2")

Example: "Dec 25 to Dec 28, 2 guests" 🎯"""
                    
                    self.update_conversation_state(conversation["_id"], "details", data)
                    return booking_msg
            
            # Handle new search
            return self.handle_search(message, conversation)
            
        except Exception as e:
            logger.error(f"Booking error: {e}")
            return "Please select 1, 2, or 3, or search again."
    
    def handle_details(self, message, conversation):
        """Handle booking details"""
        try:
            data = conversation.get("data", {})
            listing = data["selected_listing"]
            
            # Parse booking details (simplified)
            booking_info = self.parse_booking_details(message)
            
            if booking_info:
                # Create booking
                booking_id = self.create_booking(listing, booking_info, conversation["phone_number"])
                
                if booking_id:
                    confirmation_msg = f"""🎉 *Booking Confirmed!*

📋 *Booking ID:* {booking_id}
🏡 *Property:* {listing['title']}
📅 *Dates:* {booking_info.get('dates', 'As requested')}
👥 *Guests:* {booking_info.get('guests', '2')}
💰 *Total:* ₹{booking_info.get('total', listing['price'] * 2):,}

📧 *Payment Link:* https://villagestay.com/pay/{booking_id}

The host will contact you soon! ✨

Need help? Just message me! 📱"""
                    
                    self.update_conversation_state(conversation["_id"], "completed", {})
                    return confirmation_msg
            
            return """Please provide booking details:

📅 Dates (e.g., "Dec 25 to Dec 28")
👥 Guests (e.g., "2 guests")

Example: "Dec 25 to Dec 28, 2 guests" ✍️"""
            
        except Exception as e:
            logger.error(f"Details error: {e}")
            return "Please provide dates and number of guests."
    
    def search_listings(self, query):
        """Search listings in database"""
        try:
            # Simple MongoDB search
            search_regex = {"$regex": query.lower(), "$options": "i"}
            
            listings = db.listings.find({
                "$or": [
                    {"title": search_regex},
                    {"location": search_regex},
                    {"description": search_regex}
                ],
                "is_active": True,
                "is_approved": True
            }).limit(5)
            
            results = []
            for listing in listings:
                results.append({
                    "id": str(listing["_id"]),
                    "title": listing["title"],
                    "location": listing["location"],
                    "price": listing["price_per_night"],
                    "rating": listing.get("rating", 4.5),
                    "type": listing.get("property_type", "homestay")
                })
            
            # If no results, return mock data for demo
            if not results:
                results = self.get_mock_listings(query)
            
            return results
            
        except Exception as e:
            logger.error(f"Search database error: {e}")
            return self.get_mock_listings(query)
    
    def get_mock_listings(self, query):
        """Return mock listings for demo"""
        mock_listings = [
            {
                "id": "mock_1",
                "title": "Peaceful Village Retreat",
                "location": "Rural Goa",
                "price": 2500,
                "rating": 4.8,
                "type": "homestay"
            },
            {
                "id": "mock_2", 
                "title": "Traditional Farm Experience",
                "location": "Kerala Backwaters",
                "price": 3000,
                "rating": 4.6,
                "type": "farmstay"
            },
            {
                "id": "mock_3",
                "title": "Heritage Village Stay",
                "location": "Rajasthan Desert",
                "price": 3500,
                "rating": 4.7,
                "type": "heritage_home"
            }
        ]
        return mock_listings
    
    def parse_booking_details(self, message):
        """Parse booking details from message"""
        # Simple parsing logic
        info = {}
        
        # Extract numbers for guests
        numbers = re.findall(r'\d+', message)
        if numbers:
            info["guests"] = numbers[-1]  # Assume last number is guests
        
        # Extract dates (simplified)
        if "to" in message.lower():
            info["dates"] = message
            info["total"] = 5000  # Mock total
        
        return info if info else None
    
    def create_booking(self, listing, booking_info, phone_number):
        """Create booking in database"""
        try:
            booking_doc = {
                "listing_id": listing["id"],
                "phone_number": phone_number,
                "booking_details": booking_info,
                "status": "confirmed",
                "created_at": datetime.utcnow(),
                "whatsapp_booking": True,
                "booking_reference": f"WA{datetime.now().strftime('%Y%m%d')}{hash(phone_number) % 10000:04d}"
            }
            
            result = db.bookings.insert_one(booking_doc)
            return booking_doc["booking_reference"]
            
        except Exception as e:
            logger.error(f"Booking creation error: {e}")
            return None

# Initialize bot
bot = WhatsAppBot()

@app.route('/')
def health_check():
    return jsonify({
        "status": "WhatsApp Webhook Service Running",
        "timestamp": datetime.utcnow().isoformat(),
        "phone_number": Config.WHATSAPP_FROM_NUMBER
    })

@app.route('/webhook', methods=['GET'])
def verify_webhook():
    """Webhook verification for Twilio"""
    return "Webhook verified", 200

@app.route('/webhook', methods=['POST'])
def handle_webhook():
    """Handle incoming WhatsApp messages"""
    try:
        # Log incoming request
        logger.info(f"Webhook received: {request.form.to_dict()}")
        
        # Get message data from Twilio
        from_number = request.form.get('From')
        message_body = request.form.get('Body', '').strip()
        
        if not from_number or not message_body:
            logger.warning("Missing From or Body in webhook")
            return "Missing required fields", 400
        
        logger.info(f"Message from {from_number}: {message_body}")
        
        # Process message with bot
        response_message = bot.process_message(from_number, message_body)
        
        # Send response back via Twilio
        if response_message:
            send_whatsapp_message(from_number, response_message)
        
        return "OK", 200
        
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return f"Error: {str(e)}", 500

@app.route('/send-message', methods=['POST'])
def send_test_message():
    """Send test message"""
    try:
        data = request.get_json()
        to_number = data.get('to_number')
        message = data.get('message')
        
        success = send_whatsapp_message(f"whatsapp:{to_number}", message)
        
        return jsonify({
            "success": success,
            "message": "Message sent" if success else "Failed to send"
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def send_whatsapp_message(to_number, message):
    """Send WhatsApp message via Twilio"""
    try:
        message = twilio_client.messages.create(
            from_=Config.WHATSAPP_FROM_NUMBER,
            body=message,
            to=to_number
        )
        
        logger.info(f"Message sent successfully: {message.sid}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to send message: {e}")
        return False

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 6000))
    app.run(host='0.0.0.0', port=port, debug=False)
    app = Flask()