"""The formula registry: formulas from the documents (and admin-added ones), each with every variable defined, a safe calculator,
and the question matcher the formula agent uses.

    expr      a small safe expression evaluator (nothing but arithmetic runs)       calc      inputs -> outputs, steps, warnings
    validate  the rules a formula must meet before it is saved                      store     SQLite storage, history, who may change what
    ask       question -> which formula, which numbers, what is still missing
"""
