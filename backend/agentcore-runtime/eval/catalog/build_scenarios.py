#!/usr/bin/env python3
"""Writes scenarios.jsonl: the labeled set for the synthetic catalog.

Each scenario is a short conversation run through the real turn pipeline. A turn's
labels name the answer each question should give when it is asked on that turn
(questions not asked on a turn are not scored):

  intent, recipient, gender, department, category, budget_band, offer_reply
  match   True when the catalog has something that fits the request, False when not
          (scored against the best_fit none_of_these probability, ADR-21)

'-' is not_mentioned. A turn's 'setup' sets the state before it runs:
  offer  a pending offer ('go_back', 'place_order', 'start_over', 'add_open', 'widen:<slot>')
  open   open the shelf item with this number first

source: 'live' sentences were spoken by a shopper in the first live session on this
catalog (2026-10-19); 'authored' sentences were written for this set. Labels were
written before any run. split: every third scenario is held out ('heldout').
"""
import json
from pathlib import Path

N = '-'
S = []   # (source, [(sentence, labels, setup)])


def sc(source, *turns):
    S.append((source, [t if len(t) == 3 else (*t, {}) for t in turns]))


# ---- live session (2026-10-19), as spoken; labels by intent of the shopper
sc('live', ('hey i want to buy a gift for my brother', {'intent': 'narrowing', 'recipient': 'brother', 'department': N, 'budget_band': N, 'match': True}),
   ('he likes leather wallets', {'intent': 'narrowing', 'department': 'Bags & Accessories', 'category': 'Wallets', 'match': True}),
   ('yeah the brown color ones are good', {'intent': 'narrowing', 'match': True}),
   ('can you show me the previous collection', {'intent': 'go_back'}),
   ('can you scroll down', {'intent': 'move_screen'}),
   ('can you show me some t shirts', {'intent': 'narrowing', 'category': 'T-Shirts', 'match': True}),
   ("let's go with number six i like that", {'intent': 'pointing'}),
   ('oh sorry i was uh it was number five not number six', {'intent': 'pointing'}),
   ('can you close this', {'intent': 'close_item'}, {'open': 5}))
sc('live', ('i want to buy some earrings for my wife', {'intent': 'narrowing', 'recipient': 'wife', 'department': 'Jewelry & Watches', 'category': 'Earrings', 'match': True}),
   ('nice the purple ones are good can you show me more of those', {'intent': 'narrowing', 'match': True}),
   ('something in indian tradition', {'intent': 'narrowing', 'match': True}),
   ('yeah can you look for some bracelet', {'intent': 'narrowing', 'category': 'Bracelets', 'match': True}),
   ('these are earrings i said bracelets', {'intent': 'narrowing'}),
   ('i like the first one', {'intent': 'pointing'}))
sc('live', ("i'm looking for a gift for my wife", {'intent': 'narrowing', 'recipient': 'wife', 'department': N, 'match': True}),
   ('do you have any different perfume scents?', {'intent': 'narrowing', 'department': 'Sports & Beauty', 'category': 'Fragrance', 'match': True}))
sc('live', ('hey i want to get a gift for my friend sergio', {'intent': 'narrowing', 'recipient': 'friend', 'department': N, 'match': True}),
   ('he likes electronics items', {'intent': 'narrowing', 'gender': 'men', 'department': 'Electronics Accessories', 'match': True}),
   ('these headphones are cool the fourth one', {'intent': 'pointing'}),
   ('show me some t shirts', {'intent': 'narrowing', 'match': True}),
   ('no reset the category and show me the t shirts', {'intent': 'narrowing'}))
sc('live', ('maybe the earlier ones', {'offer_reply': 'new_request'}, {'offer': 'add_open', 'open': 1}),
   ('yeah', {'offer_reply': 'yes'}, {'offer': 'go_back'}))
sc('live', ('yes', {'offer_reply': 'yes'}, {'offer': 'go_back'}))
sc('live', ('yes add this', {'offer_reply': 'yes'}, {'offer': 'add_open', 'open': 2}))
sc('live', ('no wait i need more i want to buy some earrings for my wife', {'offer_reply': 'no'}, {'offer': 'place_order'}))
sc('live', ('no add number five i like number five', {'offer_reply': 'no'}, {'offer': 'go_back'}))

# ---- authored: one or two scenarios per department, with recipients, budgets and changes of mind
sc('authored', ('a birthday present for my mom', {'intent': 'narrowing', 'recipient': 'mother', 'department': N, 'budget_band': N, 'match': True}),
   ('she loves summer dresses', {'intent': 'narrowing', 'department': "Women's Clothing", 'category': 'Dresses', 'match': True}),
   ('something under 60 dollars', {'intent': 'narrowing', 'match': True}),
   ('the third one looks lovely', {'intent': 'pointing'}),
   ('what is it made of', {'intent': 'other'}))
sc('authored', ('i need a pair of jeans for myself', {'intent': 'narrowing', 'recipient': 'self', 'department': "Women's Clothing", 'category': 'Jeans', 'match': True}),
   ('high waisted and dark blue', {'intent': 'narrowing', 'match': True}),
   ('actually make it a skirt instead', {'intent': 'narrowing', 'category': 'Skirts', 'match': True}))
sc('authored', ('a cozy sweater for my girlfriend', {'intent': 'narrowing', 'recipient': 'girlfriend', 'department': "Women's Clothing", 'category': 'Knitwear', 'match': True}),
   ('nothing too pricey, maybe around 50 to 100', {'intent': 'narrowing', 'match': True}))
sc('authored', ('my husband needs a new dress shirt for work', {'intent': 'narrowing', 'recipient': 'husband', 'department': "Men's Clothing", 'category': 'Shirts', 'match': True}),
   ('something in light blue', {'intent': 'narrowing', 'match': True}),
   ('go back', {'intent': 'go_back'}))
sc('authored', ('a suit for my son’s wedding, he is an adult', {'intent': 'narrowing', 'recipient': 'son', 'department': "Men's Clothing", 'category': 'Suits & Blazers', 'match': True}),
   ('a navy one', {'intent': 'narrowing', 'match': True}))
sc('authored', ('a warm winter jacket for my dad', {'intent': 'narrowing', 'recipient': 'father', 'department': "Men's Clothing", 'category': 'Jackets', 'match': True}),
   ('under 150 please', {'intent': 'narrowing', 'match': True}))
sc('authored', ('chinos for me, i am a guy', {'intent': 'narrowing', 'recipient': 'self', 'gender': 'men', 'department': "Men's Clothing", 'category': 'Trousers', 'match': True}))
sc('authored', ('my daughter needs a party dress', {'intent': 'narrowing', 'recipient': 'daughter', 'department': 'Kids & Teens', 'category': "Girls' Dresses", 'match': True}),
   ('she is seven and loves pink', {'intent': 'narrowing', 'match': True}))
sc('authored', ('a school backpack for my son', {'intent': 'narrowing', 'recipient': 'son', 'department': 'Kids & Teens', 'category': 'School Bags', 'match': True}),
   ('one with dinosaurs on it', {'intent': 'narrowing', 'match': True}))
sc('authored', ('a rain jacket for my little boy', {'intent': 'narrowing', 'recipient': 'son', 'gender': 'boys', 'department': 'Kids & Teens', 'category': "Kids' Outerwear", 'match': True}))
sc('authored', ('a hoodie for my teenage niece, something streetwear', {'intent': 'narrowing', 'recipient': 'other', 'gender': 'girls', 'department': 'Kids & Teens', 'category': 'Teen Streetwear', 'match': True}))
sc('authored', ('running shoes for me', {'intent': 'narrowing', 'recipient': 'self', 'department': 'Shoes', 'category': 'Sneakers', 'match': True}),
   ('for women, size does not matter', {'intent': 'narrowing', 'gender': 'women', 'match': True}),
   ('in white', {'intent': 'narrowing', 'match': True}))
sc('authored', ('leather boots for my brother', {'intent': 'narrowing', 'recipient': 'brother', 'department': 'Shoes', 'category': 'Boots', 'match': True}),
   ('actually he would prefer sandals', {'intent': 'narrowing', 'category': 'Sandals', 'match': True}))
sc('authored', ('formal shoes for a wedding, for my wife', {'intent': 'narrowing', 'recipient': 'wife', 'department': 'Shoes', 'category': 'Formal Shoes', 'match': True}))
sc('authored', ('light up shoes for my daughter', {'intent': 'narrowing', 'recipient': 'daughter', 'department': 'Shoes', 'category': "Kids' Shoes", 'match': True}))
sc('authored', ('a handbag for my sister', {'intent': 'narrowing', 'recipient': 'sister', 'department': 'Bags & Accessories', 'category': 'Handbags', 'match': True}),
   ('a crossbody one under 100 dollars', {'intent': 'narrowing', 'match': True}),
   ('the second one please', {'intent': 'pointing'}),
   ('add it to my cart', {'intent': 'confirm'}, {'open': 2}))
sc('authored', ('a belt for my boyfriend', {'intent': 'narrowing', 'recipient': 'boyfriend', 'department': 'Bags & Accessories', 'category': 'Belts', 'match': True}),
   ('a brown leather one', {'intent': 'narrowing', 'match': True}))
sc('authored', ('a winter hat and scarf for my grandmother', {'intent': 'narrowing', 'recipient': 'grandmother', 'department': 'Bags & Accessories', 'category': 'Scarves & Hats', 'match': True}))
sc('authored', ('a laptop backpack for my coworker', {'intent': 'narrowing', 'recipient': 'coworker', 'department': 'Bags & Accessories', 'category': 'Backpacks', 'match': True}))
sc('authored', ('a necklace for my mom', {'intent': 'narrowing', 'recipient': 'mother', 'department': 'Jewelry & Watches', 'category': 'Necklaces', 'match': True}),
   ('with pearls', {'intent': 'narrowing', 'match': True}),
   ('show me watches instead', {'intent': 'narrowing', 'category': 'Watches', 'match': True}))
sc('authored', ('a ring for my husband', {'intent': 'narrowing', 'recipient': 'husband', 'department': 'Jewelry & Watches', 'category': 'Rings', 'match': True}),
   ('something over 200 dollars', {'intent': 'narrowing', 'match': True}))
sc('authored', ('a watch for my 10 year old son', {'intent': 'narrowing', 'recipient': 'son', 'gender': 'boys', 'department': 'Jewelry & Watches', 'category': 'Watches', 'match': True}))
sc('authored', ('a puzzle for my nephew', {'intent': 'narrowing', 'recipient': 'other', 'department': 'Toys & Games', 'category': 'Puzzles & Cubes', 'match': True}),
   ('maybe a speed cube', {'intent': 'narrowing', 'match': True}))
sc('authored', ('a board game for family night', {'intent': 'narrowing', 'department': 'Toys & Games', 'category': 'Board Games', 'match': True}))
sc('authored', ('a teddy bear for my daughter', {'intent': 'narrowing', 'recipient': 'daughter', 'department': 'Toys & Games', 'category': 'Plush Toys', 'match': True}),
   ('or a building set', {'intent': 'narrowing', 'category': 'Building Sets', 'match': True}))
sc('authored', ('something to play with in the garden for my kids', {'intent': 'narrowing', 'recipient': 'child', 'department': 'Toys & Games', 'category': 'Outdoor Toys', 'match': True}))
sc('authored', ('a coffee mug for my coworker', {'intent': 'narrowing', 'recipient': 'coworker', 'department': 'Home & Kitchen', 'category': 'Mugs & Drinkware', 'match': True}),
   ('under 20 dollars', {'intent': 'narrowing', 'match': True}))
sc('authored', ('a non stick frying pan', {'intent': 'narrowing', 'department': 'Home & Kitchen', 'category': 'Cookware', 'match': True}))
sc('authored', ('new bed sheets for my parents', {'intent': 'narrowing', 'recipient': 'parent', 'department': 'Home & Kitchen', 'category': 'Bedding', 'match': True}))
sc('authored', ('a reading lamp for my desk', {'intent': 'narrowing', 'recipient': 'self', 'department': 'Home & Kitchen', 'category': 'Lamps', 'match': True}),
   ('or maybe a vase', {'intent': 'narrowing', 'category': 'Home Decor', 'match': True}))
sc('authored', ('wireless earbuds for my girlfriend', {'intent': 'narrowing', 'recipient': 'girlfriend', 'department': 'Electronics Accessories', 'category': 'Headphones', 'match': True}),
   ('under 100 dollars', {'intent': 'narrowing', 'match': True}),
   ('the first one', {'intent': 'pointing'}))
sc('authored', ('a case for my iphone', {'intent': 'narrowing', 'recipient': 'self', 'department': 'Electronics Accessories', 'category': 'Phone Cases', 'match': True}))
sc('authored', ('a portable charger for travel', {'intent': 'narrowing', 'department': 'Electronics Accessories', 'category': 'Chargers & Power', 'match': True}),
   ('actually a bluetooth speaker', {'intent': 'narrowing', 'category': 'Speakers', 'match': True}))
sc('authored', ('a fitness tracker for my dad', {'intent': 'narrowing', 'recipient': 'father', 'department': 'Electronics Accessories', 'category': 'Smartwatches & Trackers', 'match': True}))
sc('authored', ('a yoga mat for me', {'intent': 'narrowing', 'recipient': 'self', 'department': 'Sports & Beauty', 'category': 'Yoga & Fitness', 'match': True}),
   ('and a water bottle', {'intent': 'narrowing', 'category': 'Water Bottles', 'match': True}))
sc('authored', ('a cycling helmet for my husband', {'intent': 'narrowing', 'recipient': 'husband', 'department': 'Sports & Beauty', 'category': 'Cycling Gear', 'match': True}))
sc('authored', ('a skincare gift set for my aunt', {'intent': 'narrowing', 'recipient': 'aunt', 'department': 'Sports & Beauty', 'category': 'Skincare Sets', 'match': True}))
sc('authored', ('cologne for my grandfather', {'intent': 'narrowing', 'recipient': 'grandfather', 'department': 'Sports & Beauty', 'category': 'Fragrance', 'match': True}),
   ('under 25 dollars', {'intent': 'narrowing', 'match': True}))
sc('authored', ('something nice for my partner', {'intent': 'narrowing', 'recipient': 'partner', 'department': N, 'match': True}),
   ('maybe jewelry', {'intent': 'narrowing', 'department': 'Jewelry & Watches', 'match': True}))
sc('authored', ('i am just browsing', {'intent': 'narrowing', 'recipient': N, 'department': N, 'match': True}))
sc('authored', ('a cheap gift for a friend, under 25', {'intent': 'narrowing', 'recipient': 'friend', 'department': N, 'match': True}))
sc('authored', ('something between 100 and 200 dollars for my wife', {'intent': 'narrowing', 'recipient': 'wife', 'match': True}))
sc('authored', ('a gift for my uncle who loves cooking', {'intent': 'narrowing', 'recipient': 'uncle', 'department': 'Home & Kitchen', 'category': 'Cookware', 'match': True}))
sc('authored', ('a scarf for my mother', {'intent': 'narrowing', 'recipient': 'mother', 'department': 'Bags & Accessories', 'category': 'Scarves & Hats', 'match': True}),
   ('start over', {'intent': 'start_over'}),
   ('a wallet for my dad', {'intent': 'narrowing', 'recipient': 'father', 'department': 'Bags & Accessories', 'category': 'Wallets', 'match': True}))
sc('authored', ('a phone case', {'intent': 'narrowing', 'department': 'Electronics Accessories', 'category': 'Phone Cases', 'match': True}),
   ('scroll down a bit', {'intent': 'move_screen'}),
   ('take me back to the top', {'intent': 'move_screen'}),
   ('the seventh one', {'intent': 'pointing'}),
   ('close it', {'intent': 'close_item'}, {'open': 7}))
sc('authored', ('earrings for my girlfriend', {'intent': 'narrowing', 'recipient': 'girlfriend', 'department': 'Jewelry & Watches', 'category': 'Earrings', 'match': True}),
   ('i am ready to check out', {'intent': 'place_order'}),
   ('hide this product', {'intent': 'close_item'}, {'open': 1}))

# ---- out of catalog: nothing in the store fits (no-match should fire)
for sentence in ['a sofa for my living room', 'a lawn mower', 'dog food for my puppy', 'a gaming laptop', 'an electric guitar',
                 'car seat covers', 'a garden hose', 'a road bike', 'a silk tie for my husband', 'a winter tent for camping',
                 'a baby stroller', 'printer ink', 'a fishing rod', 'a set of golf clubs', 'kitchen knives', 'a microwave oven']:
    sc('authored', (sentence, {'intent': 'narrowing', 'match': False}))
sc('authored', ('a gift for my mom', {'intent': 'narrowing', 'recipient': 'mother', 'match': True}),
   ('she needs a new vacuum cleaner', {'intent': 'narrowing', 'match': False}))
sc('authored', ('something for my son', {'intent': 'narrowing', 'recipient': 'son', 'match': True}),
   ('he wants a video game console', {'intent': 'narrowing', 'match': False}))
sc('authored', ('jewelry for my wife', {'intent': 'narrowing', 'recipient': 'wife', 'department': 'Jewelry & Watches', 'match': True}),
   ('a diamond tiara', {'intent': 'narrowing', 'match': False}))

# ---- replies to offers
for offer, yes, no, new in [
        ('go_back', ['yeah', 'yes please', 'sure go back', 'okay', 'yep'], ['no', 'nope', 'no thanks', 'no keep these'], ['show me red dresses', 'the fourth one', 'can you close this']),
        ('place_order', ['yes place it', 'go ahead', 'yeah do it', 'sure'], ['not yet', 'no wait', 'hold on no'], ['add a belt too', 'show me some hats']),
        ('start_over', ['yes start over', 'yeah clear it', 'please do'], ['no keep my choices', 'no'], ['a wallet for my dad']),
        ('add_open', ['yeah add this', 'yes please', 'sure add it', 'definitely'], ['no not this one', 'no thanks'], ['what is it made of', 'show me the blue one instead', 'the next one']),
        ('widen:category', ['yes', 'yeah look further', 'sure'], ['no', 'no keep the category'], ['try a different color'])]:
    for label, sentences in (('yes', yes), ('no', no), ('new_request', new)):
        for s in sentences:
            setup = {'offer': offer}
            if offer == 'add_open':
                setup['open'] = 1
            sc('authored', ('a gift for my mom', {}), (s, {'offer_reply': label}, setup))

out = Path(__file__).with_name('scenarios.jsonl')
with out.open('w') as f:
    for i, (source, turns) in enumerate(S):
        f.write(json.dumps({'id': f's{i:03d}', 'source': source, 'split': 'heldout' if i % 3 == 2 else 'tuning',
                            'turns': [{'say': s, 'labels': l, 'setup': u} for s, l, u in turns]}) + '\n')
labeled = sum(1 for _, t in S for x in t for _ in x[1])
print(f'{len(S)} scenarios, {sum(len(t) for _, t in S)} turns, {labeled} labels -> {out}')
