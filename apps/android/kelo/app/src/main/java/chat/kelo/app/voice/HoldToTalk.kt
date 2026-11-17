package chat.kelo.app.voice

/**
 * The mic button's finger rules, kept apart from the screen so they can be tested. A short touch is a tap (today's start / stop);
 * holding it for [HOLD_MS] starts dictating; letting go on the button finishes (the words stay in the field, nothing is sent); letting go
 * after sliding off the button cancels (the field goes back to what it was).
 */
object HoldToTalk {
    /** How long the finger must stay down before it is a hold and not a tap. */
    const val HOLD_MS = 350L

    enum class Phase {
        IDLE,
        /** finger down, not yet a hold */
        PRESSED,
        /** finger down while listening already (started by a tap): a lift is the tap that stops it */
        PRESSED_LISTENING,
        /** held: dictating, the finger is on the button */
        HOLDING,
        /** held: dictating, the finger slid off the button, so lifting cancels */
        HOLDING_AWAY,
    }

    sealed interface Event {
        data class Down(val listening: Boolean) : Event
        /** the finger moved; [onButton] whether it is still on the button */
        data class Move(val onButton: Boolean) : Event
        /** [HOLD_MS] passed with the finger down */
        data object HoldElapsed : Event
        data object Up : Event
        /** the system took the touch away */
        data object Cancel : Event
    }

    enum class Action {
        NONE,
        /** a short touch: start listening, or stop when it listens */
        TAP,
        /** start dictating now (the finger stays down) */
        START,
        /** stop listening and keep the words */
        FINISH,
        /** stop listening and drop the words */
        CANCEL,
    }

    data class Step(val phase: Phase, val action: Action = Action.NONE)

    fun step(phase: Phase, event: Event): Step = when (event) {
        is Event.Down -> Step(if (event.listening) Phase.PRESSED_LISTENING else Phase.PRESSED)
        is Event.Move -> when (phase) {
            Phase.HOLDING, Phase.HOLDING_AWAY -> Step(if (event.onButton) Phase.HOLDING else Phase.HOLDING_AWAY)
            // sliding off before it became a hold: nothing happens at all
            Phase.PRESSED, Phase.PRESSED_LISTENING -> if (event.onButton) Step(phase) else Step(Phase.IDLE)
            Phase.IDLE -> Step(Phase.IDLE)
        }
        Event.HoldElapsed -> if (phase == Phase.PRESSED) Step(Phase.HOLDING, Action.START) else Step(phase)
        Event.Up -> when (phase) {
            Phase.PRESSED, Phase.PRESSED_LISTENING -> Step(Phase.IDLE, Action.TAP)
            Phase.HOLDING -> Step(Phase.IDLE, Action.FINISH)
            Phase.HOLDING_AWAY -> Step(Phase.IDLE, Action.CANCEL)
            Phase.IDLE -> Step(Phase.IDLE)
        }
        Event.Cancel -> when (phase) {
            Phase.HOLDING, Phase.HOLDING_AWAY -> Step(Phase.IDLE, Action.CANCEL)
            else -> Step(Phase.IDLE)
        }
    }

    /** Whether a touch at ([x], [y]) is still on a button of [width] x [height] (all in the same unit); [slack] around it still counts as on it. */
    fun onButton(x: Float, y: Float, width: Float, height: Float, slack: Float): Boolean =
        x >= -slack && x <= width + slack && y >= -slack && y <= height + slack
}
