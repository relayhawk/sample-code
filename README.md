# Relay Hawk Sample Code Repository

Welcome to the **Relay Hawk Sample Code Repository**! This repository showcases sample projects and integrations designed to help developers and businesses streamline workflows, enhance operational efficiency, and explore cutting-edge technologies in real-time communication, AI, and API integrations.

Relay Hawk specializes in **white-glove implementation of integrations** and **low-latency AI tools**. Our solutions help address industry pain points by seamlessly connecting tools like CRMs, ticketing systems, phone systems, and more, enabling faster resolutions of your customer's requests and improved internal workflows.

---

## **Projects in This Repository**

### [Twilio + OpenAI Real-time Audio Processing](twilio-openai/)
This project demonstrates real-time audio processing using **Twilio's Media Streams API** and **OpenAI's Realtime API**, enabling interactive AI-driven conversations over phone calls.  

#### **Key Features:**
- Real-time audio streaming from Twilio phone calls.
- Integration with OpenAI APIs for dynamic responses.
- Tool usage for AI agents.
- Secure WebSocket communication and Twilio webhook validation.
- Dockerized deployment for seamless setup.

For details, see the [Twilio + OpenAI README](twilio-openai/).

---

### [Call Transcriber: Live Transcripts of Both Sides of a Call](call-transcriber/)
A macOS command-line tool that writes a live transcript of a Zoom, Meet or Teams call, with your mic as `Me` and the call audio as `Them`. It ships with a Claude Code skill that keeps a notes doc updated during the call.

#### **Key Features:**
- Records your mic and the Mac's audio output as separate streams, so speaker sides are never mixed up. No meeting bot.
- Removes speaker echo, reconnects on its own, and writes a new file per call.
- Pluggable speech-to-text: Deepgram included, with a short interface for adding others such as Whisper.

For details, see the [Call Transcriber README](call-transcriber/).

---

### [Claude Code Customizations](claude-code/)
A growing collection of practical customizations for **Claude Code**, Anthropic's agentic coding tool.

#### **Included So Far:**
- [Subagent Status Line](claude-code/subagent-statusline/): Add the model of each subagent to the status line, so an expensive model can't quietly delegate to another expensive agent and burn your token limits.

For details, see the [Claude Code Customizations README](claude-code/).

---

## **Why This Repository?**

This repository serves as a starting point for:
- **Customers**: Explore how Relay Hawk solutions can be integrated into your existing infrastructure to improve workflows and caller experiences.
- **Developers**: Use these samples to collaborate, learn, and build innovative projects with real-world use cases.
- **Community**: Contribute to projects or suggest new ones to solve industry challenges.

---

## **Get Involved**

We encourage contributions and collaboration! Whether you're looking to learn, improve a project, or share feedback, we’d love to hear from you.

1. **Clone the repository**:
   ```
   git clone https://github.com/relayhawk/sample-code.git
   ```
2. Explore individual project folders for setup instructions and documentation.
3. Submit pull requests or issues to suggest enhancements or fixes.


