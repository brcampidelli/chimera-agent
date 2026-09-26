"""The frozen item set for bench/web_research: multi-hop questions with stable, checkable answers.

Each item names the pages a person would walk through (``sources``, in hop order) and the answer's
accepted spellings. The LAST source is the gold page: `run.py --check-gold` fetches it before any
model call and requires it to contain the answer, so an item whose answer key cannot be shown on its
own page never reaches the run (bench/PREREGISTRATION.md, "Validity of the answer key").

Stable on purpose. Every answer is a fact about the past (a birthplace, a year, a name) that no
edit to the page is expected to change, so a run next month grades against the same key.
"""

from __future__ import annotations

from dataclasses import dataclass

W = "https://en.wikipedia.org/wiki/"


@dataclass(frozen=True)
class Item:
    id: str
    question: str
    answers: tuple[str, ...]
    sources: tuple[str, ...]

    @property
    def hops(self) -> int:
        return len(self.sources)

    @property
    def gold(self) -> str:
        return self.sources[-1]


ITEMS: tuple[Item, ...] = (
    Item(
        "utzon_city",
        "The architect whose design won the 1957 competition for the Sydney Opera House was born "
        "in which city?",
        ("Copenhagen",),
        (W + "Sydney_Opera_House", W + "J%C3%B8rn_Utzon"),
    ),
    Item(
        "moai_river",
        "Which river flows through the capital city of the country that governs the Pacific "
        "island known for its moai statues?",
        ("Mapocho",),
        (W + "Easter_Island", W + "Chile", W + "Santiago"),
    ),
    Item(
        "smetana_river",
        "The composer of the opera The Bartered Bride was born in a town in Bohemia. On which "
        "river does that town lie?",
        ("Loučná", "Loucna"),
        (W + "The_Bartered_Bride", W + "Bed%C5%99ich_Smetana", W + "Litomy%C5%A1l"),
    ),
    Item(
        "fram_designer_death",
        "The ship used by the first expedition to reach the South Pole was designed by a "
        "shipbuilder. In what year did that shipbuilder die?",
        ("1921",),
        (W + "Amundsen%27s_South_Pole_expedition", W + "Fram", W + "Colin_Archer"),
    ),
    Item(
        "beagle_captain_colony",
        "The captain who commanded the ship on Charles Darwin's five-year survey voyage later "
        "became governor of which colony?",
        ("New Zealand",),
        (W + "Second_voyage_of_HMS_Beagle", W + "Robert_FitzRoy"),
    ),
    Item(
        "pascal_city",
        "The designer of the Pascal programming language was born in which Swiss city?",
        ("Winterthur",),
        (W + "Pascal_(programming_language)", W + "Niklaus_Wirth"),
    ),
    Item(
        "eris_observatory",
        "The dwarf planet whose discovery prompted the 2006 redefinition of 'planet' was found in "
        "images taken at which observatory?",
        ("Palomar",),
        (W + "IAU_definition_of_planet", W + "Eris_(dwarf_planet)"),
    ),
    Item(
        "sedna_mythology",
        "The astronomer who led the team that discovered Eris also co-discovered, in 2003, an "
        "object named after a sea goddess. From which people's mythology does that goddess come?",
        ("Inuit",),
        (W + "Eris_(dwarf_planet)", W + "Michael_E._Brown", W + "90377_Sedna"),
    ),
    Item(
        "nobel_1982_town",
        "The winner of the 1982 Nobel Prize in Literature was born in which town?",
        ("Aracataca",),
        (W + "1982_Nobel_Prize_in_Literature", W + "Gabriel_Garc%C3%ADa_M%C3%A1rquez"),
    ),
    Item(
        "phi_temple",
        "The Greek letter usually used for the golden ratio is said to honour an ancient Greek "
        "sculptor. The sculptures of which Athenian temple did that sculptor oversee?",
        ("Parthenon",),
        (W + "Golden_ratio", W + "Phidias"),
    ),
    Item(
        "fields_latam_city",
        "The first mathematician from Latin America to be awarded the Fields Medal was born in "
        "which city?",
        ("Rio de Janeiro",),
        (W + "Fields_Medal", W + "Artur_Avila"),
    ),
    Item(
        "niteroi_museum_year",
        "The architect of the Cathedral of Brasília also designed a contemporary art museum in "
        "Niterói. In what year was that museum completed?",
        ("1996",),
        (W + "Cathedral_of_Bras%C3%ADlia", W + "Oscar_Niemeyer",
         W + "Niter%C3%B3i_Contemporary_Art_Museum"),
    ),
    Item(
        "northwest_passage_vessel",
        "In which vessel did the leader of the first expedition to reach the South Pole earlier "
        "make the first traverse of the Northwest Passage?",
        ("Gjøa", "Gjoa"),
        (W + "Roald_Amundsen", W + "Gj%C3%B8a"),
    ),
    Item(
        "piano_inventor_city",
        "The man generally credited with inventing the piano was born in which Italian city?",
        ("Padua", "Padova"),
        (W + "Piano", W + "Bartolomeo_Cristofori"),
    ),
    Item(
        "quixote_battle",
        "The author of Don Quixote lost the use of his left hand in which naval battle?",
        ("Lepanto",),
        (W + "Don_Quixote", W + "Miguel_de_Cervantes"),
    ),
    Item(
        "flowmatic_destroyer",
        "A US Navy destroyer is named after the computer scientist who led the team that created "
        "the FLOW-MATIC language. What is that destroyer's hull number?",
        ("DDG-70", "DDG 70"),
        (W + "FLOW-MATIC", W + "Grace_Hopper", W + "USS_Hopper"),
    ),
    Item(
        "tesla_country",
        "The engineer after whom the SI unit of magnetic flux density is named was born in a "
        "village that lies in which present-day country?",
        ("Croatia",),
        (W + "Tesla_(unit)", W + "Nikola_Tesla"),
    ),
    Item(
        "angel_falls_state",
        "The aviator after whom the world's tallest uninterrupted waterfall is named was born in "
        "which US state?",
        ("Missouri",),
        (W + "Angel_Falls", W + "Jimmie_Angel"),
    ),
    Item(
        "liberty_engineer_city",
        "The engineer whose company built the internal iron framework of the Statue of Liberty "
        "was born in which French city?",
        ("Dijon",),
        (W + "Statue_of_Liberty", W + "Gustave_Eiffel"),
    ),
    Item(
        "mayflower_master_death",
        "The master of the ship that carried the Pilgrims to Plymouth in 1620 died in which year?",
        ("1622",),
        (W + "Mayflower", W + "Christopher_Jones_(Mayflower_captain)"),
    ),
    Item(
        "fluorine_nobel_year",
        "The chemist who first isolated elemental fluorine received the Nobel Prize in Chemistry "
        "in which year?",
        ("1906",),
        (W + "Fluorine", W + "Henri_Moissan"),
    ),
    Item(
        "neptune_moon_discoverer",
        "Who discovered the largest moon of the planet whose position was predicted "
        "mathematically by Urbain Le Verrier?",
        ("Lassell",),
        (W + "Urbain_Le_Verrier", W + "Neptune", W + "Triton_(moon)"),
    ),
)

#: Amendment 1 (PREREGISTRATION.md): a second stratum, registered after the pilot found both arms at
#: 3/3 on the first three items and before any main-run call. Same construction and the same gold
#: check, but each answer is a detail near the end of a 2-4 page chain that a model is unlikely to
#: know without reading it: a supervisor, a village, a launch year, the name of a plane.
HARD: tuple[Item, ...] = (
    Item(
        "archer_birthplace",
        "The shipbuilder who designed the vessel used by the first expedition to reach the South "
        "Pole was born in which Norwegian town?",
        ("Larvik",),
        (W + "Amundsen%27s_South_Pole_expedition", W + "Fram", W + "Colin_Archer"),
    ),
    Item(
        "angel_plane",
        "What was the name of the aircraft that the aviator after whom the world's tallest "
        "uninterrupted waterfall is named landed on top of the mountain?",
        ("Rio Caroni",),
        (W + "Angel_Falls", W + "Jimmie_Angel"),
    ),
    Item(
        "wirth_supervisor",
        "Who supervised the doctoral thesis of the designer of the Pascal programming language?",
        ("Huskey",),
        (W + "Pascal_(programming_language)", W + "Niklaus_Wirth"),
    ),
    Item(
        "hopper_launch_year",
        "The US Navy destroyer named after the computer scientist who led the team that created "
        "the FLOW-MATIC language was launched in which year?",
        ("1996",),
        (W + "FLOW-MATIC", W + "Grace_Hopper", W + "USS_Hopper"),
    ),
    Item(
        "avila_advisor",
        "Who was the doctoral advisor of the first mathematician from Latin America to be awarded "
        "the Fields Medal?",
        ("Welington",),
        (W + "Fields_Medal", W + "Artur_Avila"),
    ),
    Item(
        "winterthur_river",
        "A small river flows through the middle of the Swiss city where the designer of the Pascal "
        "programming language was born. What is that river called?",
        ("Eulach",),
        (W + "Pascal_(programming_language)", W + "Niklaus_Wirth", W + "Winterthur"),
    ),
    Item(
        "smiljan_town",
        "The village where the engineer after whom the SI unit of magnetic flux density is named "
        "was born lies a few kilometres from which town?",
        ("Gospić", "Gospic"),
        (W + "Tesla_(unit)", W + "Nikola_Tesla", W + "Smiljan,_Croatia"),
    ),
    Item(
        "litomysl_unesco_year",
        "The castle in the birth town of the composer of The Bartered Bride was declared a UNESCO "
        "World Heritage Site in which year?",
        ("1999",),
        (W + "The_Bartered_Bride", W + "Bed%C5%99ich_Smetana", W + "Litomy%C5%A1l",
         W + "Litomy%C5%A1l_Castle"),
    ),
    Item(
        "aracataca_department",
        "The birth town of the winner of the 1982 Nobel Prize in Literature lies in which "
        "Colombian department?",
        ("Magdalena",),
        (W + "1982_Nobel_Prize_in_Literature", W + "Gabriel_Garc%C3%ADa_M%C3%A1rquez",
         W + "Aracataca"),
    ),
    Item(
        "fitzroy_death_place",
        "The captain of the ship on Charles Darwin's five-year survey voyage died in which part "
        "of London?",
        ("Norwood",),
        (W + "Second_voyage_of_HMS_Beagle", W + "Robert_FitzRoy"),
    ),
    Item(
        "lassell_trade",
        "The astronomer who discovered the largest moon of Neptune made his fortune in which "
        "trade?",
        ("brewer", "brewing", "brewery", "beer"),
        (W + "Neptune", W + "Triton_(moon)", W + "William_Lassell"),
    ),
    Item(
        "gjoa_builder_village",
        "The vessel in which the first person to reach the South Pole traversed the Northwest "
        "Passage was built in which Norwegian village?",
        ("Rosendal",),
        (W + "Roald_Amundsen", W + "Gj%C3%B8a"),
    ),
    Item(
        "cristofori_patron",
        "In 1688 the man credited with inventing the piano was recruited to work for a prince. "
        "Which prince?",
        ("Ferdinando",),
        (W + "Piano", W + "Bartolomeo_Cristofori"),
    ),
    Item(
        "mayflower_master_birthplace",
        "The master of the ship that carried the Pilgrims to Plymouth in 1620 was born in which "
        "English town?",
        ("Harwich",),
        (W + "Mayflower", W + "Christopher_Jones_(Mayflower_captain)"),
    ),
)

#: Both strata, in run order: the registered set first.
ALL: tuple[Item, ...] = ITEMS + HARD
