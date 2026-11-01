"""What each named agent on the Agents page answers, and the one-line reply when a question belongs to someone else.

The question planner (answering/facts.py) already says what kind of task a question is: Troubleshooting, Safety, Procedure, Standards, Selection, Parameters, Equipment.
Fault Finder takes the first two, Procedure Advisor the next three. Parameters and Equipment questions belong to the ordinary Chat. A question the planner puts in no task
(or in one of the agent's own tasks) is answered as usual."""

AGENTS = {
    "fault-finder": ("Fault Finder", frozenset({"Troubleshooting", "Safety"})),
    "procedure-advisor": ("Procedure Advisor", frozenset({"Procedure", "Standards", "Selection"})),
}
CHAT = "Chat"


def _owner(task):
    for _key, (name, tasks) in AGENTS.items():
        if task in tasks:
            return name
    return CHAT


def redirect(agent, tasks):
    """-> "<Agent> can't answer this part. This is a <task> question. Use <Other>." when none of the question's tasks are the agent's own, else None."""
    if agent not in AGENTS or not tasks:
        return None
    name, own = AGENTS[agent]
    if any(task in own for task in tasks):
        return None
    task = tasks[0]
    return f"{name} can't answer this part. This is a {task.lower()} question. Use {_owner(task)}."
