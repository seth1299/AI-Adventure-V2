# AI Adventure

AI Adventure is a Python desktop application for building an open-ended, stateful text adventure game with a Qt interface, structured game state, Markdown-driven rules, and eventual Google Gemini integration.

The project is currently being rebuilt from a simpler, cleaner foundation. The immediate goal is to make the core application work without relying on an AI model first. Once the UI, save system, game state, event models, reducers, and history pipeline are stable, the AI layer can be added on top as a controlled narrator instead of being forced to remember every rule, state detail, and system instruction at once.

## Project Ambition

The long-term goal is to create a one-file executable text adventure engine where the player can explore a persistent fantasy world, manage inventory, discover alchemical recipes, inspect state, review history, and interact with an AI narrator that understands the current adventure context without being overloaded by unnecessary information.

The project is designed around a few core ambitions:

- Build a playable, stateful adventure application in Python.
- Keep the application distributable as a one-file `.exe`.
- Use Qt for a structured desktop GUI instead of a plain terminal loop.
- Store game rules, alchemy systems, lore, and prompts in Markdown files where possible.
- Avoid overloading the AI model with huge static instructions every turn.
- Let the Python application control state, validation, history, and persistence.
- Let the AI focus on narration, world response, and creative interpretation.
- Make game state inspectable and debuggable during development.
- Keep the architecture maintainable enough to expand into a larger RPG-style system.

## Current Design Direction

The project is being simplified into a deterministic application core first. The AI will come later.

Instead of asking the AI to remember and mutate the entire world by itself, the application will own the source of truth. The AI can suggest narrative results or structured events, but reducers and state managers will decide what actually changes.

This means the project should favor:

- Explicit event models.
- Reducer-based state updates.
- Serializable game state.
- Clear save and load boundaries.
- Structured history entries.
- Small context packets for the AI.
- Markdown files for rules, lore, alchemy data, and prompt modules.
- Logging that helps diagnose state, save, prompt, and API issues.

## Planned Screens

The first working version of the rebuilt application should include a Main Menu and six core screens.

### Main Menu

The Main Menu should provide the entry point into the application.

Planned features:

- Start a new game.
- Load an existing game.
- Create an initial save file.
- Route the player into the main application shell.

### Story

The Story screen is the primary play interface.

Planned features:

- Display the current story text.
- Accept player commands or choices.
- Show recent narrative output.
- Eventually send controlled context packets to Gemini.
- Receive AI narration or structured event suggestions.
- Commit validated events into the game state.

### State Inspector

The State Inspector is a development and debugging screen.

Planned features:

- Display the current adventure state.
- Show player status, world state, flags, inventory, and active systems.
- Make state changes easier to verify while reducers are being developed.
- Help diagnose save/load and event application issues.

### Inventory

The Inventory screen should show what the player currently has.

Planned features:

- Display items owned by the player.
- Show quantities, item categories, and descriptions.
- Support item-added and item-removed events.
- Eventually support usable items, quest items, tools, ingredients, and equipment.

### Alchemy Notebook

The Alchemy Notebook is where the player tracks reagents, recipes, and discovered alchemical knowledge.

Planned features:

- Display known reagents.
- Display known recipes.
- Track discovered qualities, motions, virtues, and uses.
- Support recipe discovery through events.
- Eventually integrate with the adventure rules for experimentation and crafting.

### History

The History screen should provide an inspectable timeline of what has happened.

Planned features:

- Display narrative history.
- Display mechanical event history.
- Preserve important player actions.
- Help reconstruct context for AI prompts.
- Help debug why the current game state looks the way it does.

### Settings

The Settings screen should collect runtime and user-facing configuration.

Planned features:

- Store API-related settings.
- Store display preferences.
- Store logging or debug toggles.
- Eventually support context, prompt, and AI behavior settings.

## Architecture Plan

The current architectural direction is event-driven and reducer-based.

The application should treat the game state as a structured object. Events describe what happened, and reducers apply those events to produce the next state.

A simplified flow looks like this:

```text
Player Action
    -> Command Handler
    -> Event Model(s)
    -> Reducer(s)
    -> Updated Game State
    -> Save / History / UI Refresh
    -> Optional AI Context Packet
```

This keeps the project easier to test, debug, and expand.

## Event Models

Event models should represent meaningful changes in the game.

Examples:

- `StoryAdvancedEvent`
- `InventoryItemAddedEvent`
- `InventoryItemRemovedEvent`
- `RecipeDiscoveredEvent`
- `ReagentDiscoveredEvent`
- `FlagSetEvent`
- `LocationChangedEvent`
- `PlayerNoteAddedEvent`
- `SaveCreatedEvent`

Each event should be explicit, serializable, and safe to store in the adventure history.

## Reducers

Reducers should be responsible for applying events to the current state.

A reducer should:

- Accept the current state and an event.
- Validate the event where appropriate.
- Return an updated state.
- Avoid mutating unrelated parts of the application.
- Log unexpected or invalid data safely.
- Be predictable enough to unit test later.

Reducers make the state pipeline clearer than scattering direct state mutations across GUI callbacks.

## AI Integration Plan

Gemini integration should be added after the non-AI application loop works.

The AI should not be responsible for remembering every project rule, alchemy rule, save detail, inventory item, and UI state. Instead, the application should assemble a focused context packet based on what the current interaction needs.

Possible context packet sections:

- Current scene summary.
- Recent story history.
- Relevant player state.
- Relevant inventory.
- Relevant alchemy rules.
- Relevant world rules.
- Current player command.
- Required output format.

This should make the AI more consistent and reduce the chance that long instructions cause it to forget important rules.

## Markdown Context Strategy

Markdown files should act as modular sources of truth for rules and reference material.

Possible Markdown categories:

- Default adventure rules.
- Alchemy rules.
- Reagent reference.
- World lore.
- Prompt templates.
- AI behavior guidelines.
- Scene-specific context.
- System-specific context.

The application can load only the Markdown sections that are relevant to the current interaction, instead of sending every file every time.

## Save System Goals

The save system should be reliable before the AI layer becomes complex.

A save file should eventually include:

- Adventure metadata.
- Current game state.
- Inventory state.
- Alchemy notebook state.
- History entries.
- Active flags.
- Current location or scene state.
- Settings that belong to the save.

The project should log save creation, save loading, failed commits, invalid state, and any fallback behavior.

## Logging Goals

Logging should remain centralized and useful.

The application should use Python's `logging` library to write to a single log file. Important systems should log meaningful events, warnings, and errors without flooding the log with noise.

Logging should help answer questions like:

- Was a save file created?
- Was a pending adventure committed?
- Which reducer handled an event?
- Did an event fail validation?
- Did the AI response parse correctly?
- Was a Markdown context file loaded?
- Was a default value used because data was missing?
- Did the GUI trigger the expected callback?

## Near-Term Roadmap

The current rebuild should focus on the application foundation.

### Phase 1: Bare-Bones Application

- Create the main Qt application shell.
- Add the Main Menu.
- Add the six core screens.
- Implement basic navigation.
- Implement placeholder content for each screen.
- Confirm packaging assumptions for a one-file `.exe`.

### Phase 2: Core State

- Define the adventure state model.
- Define inventory state.
- Define alchemy notebook state.
- Define history state.
- Add a state manager.
- Display state through the State Inspector.

### Phase 3: Events and Reducers

- Implement event models.
- Implement reducers.
- Route simple UI actions through events.
- Update the state through reducers only.
- Log reducer activity and invalid events.

### Phase 4: Save and Load

- Create new save files.
- Load existing save files.
- Persist state and history.
- Recover safely from missing or invalid fields.
- Log save and load activity.

### Phase 5: Markdown Context

- Load rules and reference data from Markdown files.
- Build a context selection layer.
- Prepare prompt modules without calling the AI yet.
- Verify that only relevant context is assembled.

### Phase 6: AI Narration

- Add Gemini API integration.
- Send focused context packets.
- Parse AI responses into narrative text and possible event suggestions.
- Validate AI-suggested events before applying them.
- Keep the Python application as the final authority on state changes.

### Phase 7: Alchemy Systems

- Expand reagents, qualities, motions, virtues, and recipes.
- Track discovered alchemical knowledge.
- Support notebook entries.
- Eventually support experimentation and crafting.

## Development Principles

The project should prioritize maintainability over quick hacks.

Important principles:

- Keep state changes explicit.
- Keep reducers predictable.
- Keep AI context small and relevant.
- Keep Markdown files modular.
- Keep logging centralized.
- Keep GUI callbacks thin.
- Keep save files recoverable.
- Keep systems testable where possible.
- Prefer clear abstractions over scattered conditionals.
- Build the AI layer on top of a working application, not underneath it.

## Technology Goals

Expected technology stack:

- Python
- Qt / PySide
- Google Gemini API
- Markdown files for context and rules
- JSON or similar structured save files
- Python `logging`
- One-file executable packaging

## Project Status

The project is currently in an early rebuild stage.

The priority is no longer to make the AI do everything immediately. The priority is to create a stable application foundation first:

1. Main Menu.
2. Six core screens.
3. State models.
4. Event models.
5. Reducers.
6. Save/load.
7. Markdown context loading.
8. AI integration.

This approach should make the final application more reliable, easier to debug, and less dependent on the AI remembering a massive instruction block every turn.

## Long-Term Vision

AI Adventure should become a flexible adventure engine where structured application state and AI-generated storytelling work together.

The player should feel like they are interacting with a living adventure, while the application quietly handles the mechanical truth underneath: inventory, alchemy, flags, history, saves, and context control.

The result should be a game that is more open-ended than a traditional choice-based adventure, but more stable and inspectable than a purely freeform chatbot.
