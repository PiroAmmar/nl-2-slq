## Design Context

### Users
The application serves a dual audience: both technical data professionals and non-technical business stakeholders. Their primary context is querying data efficiently, and the core job to be done is extracting actionable, verifiable insights from natural language questions without needing to write SQL or configure charts manually.

### Brand Personality
- **Voice and Tone:** Clear, authoritative, and precise.
- **3-Word Personality:** Confident, Transparent, Unembellished.
- **Emotional Goals:** The interface must emit absolute confidence that the generated answers are correct, predictable, and trustworthy.

### Aesthetic Direction
- **Visual Tone:** Refined Utilitarian with a modern, sleek edge. No superfluous animations—just snappy, standard CSS transitions.
- **Theme:** A yellow-golden accented theme that works flawlessly across Light, Dark, and System modes.
- **Anti-References:** It must explicitly NOT look like "AI-SLOP". This means avoiding generic AI chat UI clichés (like overuse of sparkles, liquid gradients, overly bouncy animations, or vague state indicators). 

### Design Principles
1. **Verifiable Confidence:** The design must visually reinforce accuracy. Prominently display source traces (e.g., PDF page references) and technical receipts (SQL used, row counts) so users trust the output.
2. **Anti-Slop Structure:** Keep the UI sharp and data-dense. Rely on crisp, structural typography (Inter for UI, JetBrains Mono for SQL/Code) and clean 1px borders rather than soft, ambiguous containers.
3. **Legibility First:** Maintain strict, accessible contrast ratios. Ensure the yellow-golden data visualization sequences remain distinct and readable against both deep dark backgrounds and stark light surfaces.
4. **Restrained Interactions:** Do not force animations. Use simple hover states and immediate feedback to make the app feel responsive and reliable, matching standard accessibility measures.
