"""English -> ER: the model drafts a design in the ER text format; only one that parses is kept.

The model's reply is never trusted as is: it goes through er.parse, which accepts only entities,
typed attributes and relationships. A parse error goes back to the model once, then the error
is shown to the user, who can fix the draft by hand.
"""

import re

from schemalab.er import ERError, Model, parse, to_text
from schemalab.llm import Gateway

PROMPT = """You turn a description of a database into an ER design written in this format:

entity Name {{ attribute type key, attribute type, ... }}
relationship Name: EntityA 1 -- N EntityB {{ attribute type }}

Rules:
- Every entity has at least one key attribute, marked with key.
- Types: int, bigint, text, varchar(n), decimal(p,s), real, double, date, time, timestamp, boolean.
- In a relationship, 1, N or M sits beside the entity it counts: Professor 1 -- N Student means one
  professor relates to N students. Use M -- N for many-to-many.
- Put ! right after an entity name when every one of its rows must take part (total participation).
- Relationship attributes go in braces after the relationship, like {{ grade text }}.
- Use singular CamelCase names. Avoid SQL words such as Order or User; say PurchaseOrder, Customer.

Example:
entity Professor {{ pid int key, name text }}
entity Student {{ sid int key, name text }}
entity Course {{ cid varchar(10) key, title text }}
relationship Advises: Professor 1 -- N Student
relationship Enrolls: Student M -- N Course! {{ grade text }}

Reply with only the design in a ``` block.

Description:
{description}
"""

RETRY = "That design does not parse: {error}\nReply with the corrected design in a ``` block."

_FENCE = re.compile(r"```[a-z]*\s*\n(.*?)```", re.DOTALL)


def design_from_english(description: str, gateway: Gateway) -> tuple[str, Model]:
    messages = [{"role": "user", "content": PROMPT.format(description=description)}]
    error = ERError("The model did not reply with a design.")
    for _ in range(2):
        reply = gateway.complete(messages).text
        match = _FENCE.search(reply)
        draft = (match[1] if match else reply).strip()
        try:
            model = parse(draft)
        except ERError as e:
            error = e
            messages += [
                {"role": "assistant", "content": reply},
                {"role": "user", "content": RETRY.format(error=e)},
            ]
            continue
        return to_text(model), model
    raise error
