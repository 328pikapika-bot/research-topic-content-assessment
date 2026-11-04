"""Versioned, source-verified inputs used only by homepage assessment cases."""

from __future__ import annotations

from typing import Any


VERIFIED_AT = "2026-10-09"


def _paper(
    title: str, doi: str, pmcid: str, year: int, authors: list[str], abstract: str,
) -> dict[str, Any]:
    return {
        "title": title,
        "doi": doi,
        "source_url": f"https://europepmc.org/articles/{pmcid}",
        "paper_id": pmcid,
        "year": year,
        "authors": authors,
        "venue": "Europe PMC",
        "abstract": abstract,
        "verified_at": VERIFIED_AT,
    }


SCENARIO_CONFIGS: dict[str, dict[str, Any]] = {
    "assessment-battery-v2": {
        "terms": ["solid state battery", "electrode electrolyte interface", "interfacial resistance", "cycling stability"],
        "papers": [
            _paper("Toward garnet electrolyte-based Li metal batteries: An ultrathin, highly effective, artificial solid-state electrolyte/metallic Li interface.", "10.1126/sciadv.1601659", "PMC5384807", 2017, ["K. K. Fu", "Y. Gong", "B. Liu", "Y. Zhu"], "Solid-state batteries are a promising option toward high energy and power densities due to the use of lithium metal as an anode. Garnet-type ceramic electrolytes are candidates because of their high ionic conductivity and stability against Li metal."),
            _paper("In Situ Generation of Poly (Vinylene Carbonate) Based Solid Electrolyte with Interfacial Stability for LiCoO2 Lithium Batteries.", "10.1002/advs.201600377", "PMC5323859", 2017, ["J. Chai", "Z. Liu", "J. Ma", "J. Wang"], "A solid polymer electrolyte with interfacial stability toward anodes and high-voltage cathodes is presented using in situ polymerization of vinylene carbonate."),
        ],
    },
    "assessment-chip-v2": {
        "terms": ["multi-organ chip", "modular connection", "barrier permeability", "microfluidic perfusion"],
        "papers": [
            _paper("Plug and Play Multi-Organ Chips: Integrated µGaskets for the Facile and Reversible Connection of Individual Organ-on-Chip Modules.", "10.3390/mi16111251", "PMC12654354", 2025, ["H. Graf", "M. Gaier", "C. Culp", "P. Loskill"], "Multi-organ-chip models allow systemic investigation of human physiology. Integration of individual organ tissues into a systemic circulation remains a challenge; modular connection strategies offer flexibility but require reliable interfaces."),
            _paper("Microfluidic organ-on-chip technology for blood-brain barrier research.", "10.1080/21688370.2016.1142493", "PMC4836466", 2016, ["M. W. van der Helm", "A. D. van der Meer", "J. C. Eijkel", "A. van den Berg"], "This review summarizes advances and challenges in blood-brain-barrier-on-chip models. Microfluidic models permit real-time study of barrier function and dysfunction for drug development."),
        ],
    },
    "assessment-crop-v2": {
        "terms": ["soybean drought tolerance", "canopy wilting", "remote sensing phenotyping", "thermal imagery"],
        "papers": [
            _paper("Multi-Scale Remote-Sensing Phenomics Integrated with Multi-Omics: Advances in Crop Drought-Heat Stress Tolerance Mechanisms and Perspectives for Climate-Smart Agriculture.", "10.3390/plants14182829", "PMC12473644", 2025, ["X. Liang", "S. Yu", "Y. Ju", "Y. Wang", "D. Yin"], "Climate change intensifies drought and heat stresses that constrain crop yields. This review describes integration of multi-scale remote-sensing phenomics with multi-omics to identify adaptive traits."),
            _paper("Identifying canopy wilting QTLs and evaluating remote sensing approaches for selecting drought-tolerant soybean.", "10.1007/s00122-025-05063-y", "PMC12521297", 2025, ["N. Burner", "P. P. A. Akiina", "Q. Song", "D. K. Harris", "Z. Li"], "A soybean population was evaluated for three years under rain-fed conditions. Aerial multispectral and thermal imagery accompanied visual canopy-wilting ratings to assess remote sensing for drought tolerance selection."),
        ],
    },
    "assessment-microplastics-v2": {
        "terms": ["soil microplastics", "plant uptake", "crop growth", "nitrogen cycling"],
        "papers": [
            _paper("Plants and microplastics: Growing impacts in the terrestrial environment.", "10.3389/fpls.2025.1666047", "PMC12518344", 2025, ["A. E. Wong", "G. Taylor"], "Microplastic pollution affects terrestrial plants and food crops. Reported effects include changes in biomass, chlorophyll, photosynthesis, metabolites and nutrients, with responses depending on exposure conditions."),
            _paper("Microplastic effects on soil nitrogen storage, nitrogen emissions, and ammonia volatilization in relation to soil health and crop productivity: mechanism and future consideration.", "10.3389/fpls.2025.1621542", "PMC12515873", 2025, ["U. Sarfraz", "Y. Qian", "Q. Yu", "Y. Cao", "J. Ding"], "This review synthesizes effects of agricultural-soil microplastics on nitrogen cycling, soil health and crop productivity, including changes in soil properties and microbial activity."),
        ],
    },
    "assessment-co2-v2": {
        "terms": ["CO2 electroreduction", "copper catalyst", "ethylene selectivity", "salt precipitation", "stability"],
        "papers": [
            _paper("Electrochemical CO2 Reduction Using Copper-Zinc and Copper-Bismuth Catalysts: Mechanistic Insights and Design Perspectives.", "10.1002/tcr.202500131", "PMC12706546", 2025, ["E. Mardones-Herrera", "I. Gamba", "M. Isaacs", "G. García"], "Electrochemical CO2 reduction offers a route to chemicals and fuels. Copper enables C1 and C2 products but has selectivity and stability limitations; this review discusses bimetallic catalyst design."),
            _paper("Thousand-hour salt precipitation-free CO2-to-ethylene electrosynthesis at high current densities.", "10.1038/s41467-025-67719-z", "PMC12847722", 2025, ["M. Fang", "Z. Huang", "M. Huang", "Z. Wang", "J. Jiang"], "Salt precipitation can block gas transport and flood electrodes during CO2-to-ethylene electroreduction. The study reports a wettability-engineered copper electrode intended to preserve interfacial hydrophobicity."),
        ],
    },
    "assessment-quantum-v2": {
        "terms": ["diamond nitrogen vacancy", "quantum sensing", "ODMR", "color center positioning", "magnetometry"],
        "papers": [
            _paper("Scalable nanoscale positioning of highly coherent color centers in prefabricated diamond nanostructures.", "10.1038/s41467-025-64758-4", "PMC12592383", 2025, ["S. Kim", "P. London", "D. Yang", "L. B. Hughes", "A. C. Bleszynski Jayich"], "Position and coherence control of diamond color centers remains a challenge. The study reports nitrogen-vacancy center creation with nanoscale localization in prefabricated diamond nanostructures."),
            _paper("Toward Subcellular Action Potential Detection with Nanodiamond Quantum Magnetometry.", "10.3390/nano15241879", "PMC12735622", 2025, ["A. Fathima", "P. Milas", "S. Mahtab", "D. Talukder", "B. Ozturk"], "Nitrogen-vacancy defects in diamond enable sensitive measurements of magnetic and electric fields. This work investigates nanodiamond optical magnetic resonance methods toward subcellular action-potential detection."),
        ],
    },
    "assessment-platform": {
        "terms": ["scientific claim verification", "retrieval augmented generation", "verifiable citation", "evidence-based report"],
        "papers": [
            _paper(
                "Valsci: an open-source, self-hostable literature review utility for automated large-batch scientific claim verification using large language models.",
                "10.1186/s12859-025-06159-4", "PMC12121171", 2025,
                ["B. Edelman", "J. Skolnick"],
                "The exponential growth of scientific publications poses a formidable challenge for researchers seeking to validate emerging hypotheses or synthesize existing evidence. In this paper, we introduce Valsci, an open-source, self-hostable utility that automates large-batch scientific claim verification using any OpenAI-compatible large language model.",
            ),
            _paper(
                "Retrieval augmented scientific claim verification.",
                "10.1093/jamiaopen/ooae021", "PMC10919922", 2024,
                ["H. Liu", "A. Soroush", "J. G. Nestor", "E. Park"],
                "To automate scientific claim verification using PubMed abstracts. We developed CliVER, an end-to-end scientific claim verification system that leverages retrieval-augmented techniques to automatically retrieve relevant clinical trial abstracts, extract pertinent sentences, and use the PICO framework to support or refute a scientific claim.",
            ),
        ],
    },
    "assessment-battery": {
        "terms": ["solid-state electrolyte", "lithium metal interface", "ionic conductivity", "interface resistance", "cycling stability"],
        "papers": [
            _paper(
                "Toward garnet electrolyte-based Li metal batteries: An ultrathin, highly effective, artificial solid-state electrolyte/metallic Li interface.",
                "10.1126/sciadv.1601659", "PMC5384807", 2017,
                ["K. K. Fu", "Y. Gong", "B. Liu", "Y. Zhu"],
                "Solid-state batteries are a promising option toward high energy and power densities due to the use of lithium (Li) metal as an anode. Among all solid electrolyte materials ranging from sulfides to oxides and oxynitrides, cubic garnet-type ceramic electrolytes are superior candidates because of their high ionic conductivity and good stability against Li metal.",
            ),
            _paper(
                "In Situ Generation of Poly (Vinylene Carbonate) Based Solid Electrolyte with Interfacial Stability for LiCoO2 Lithium Batteries.",
                "10.1002/advs.201600377", "PMC5323859", 2017,
                ["J. Chai", "Z. Liu", "J. Ma", "J. Wang"],
                "Nowadays it is extremely urgent to seek high performance solid polymer electrolyte that possesses both interfacial stability toward lithium/graphitic anodes and high voltage cathodes for high energy density solid state batteries. Inspired by the positive interfacial effect of vinylene carbonate additive on solid electrolyte interface, a novel poly (vinylene carbonate) based solid polymer electrolyte is presented via a facile in situ polymerization process in this paper.",
            ),
        ],
    },
    "assessment-drug": {
        "terms": ["explainable artificial intelligence", "drug discovery", "experimental validation", "uncertainty estimation", "virtual screening"],
        "papers": [
            _paper(
                "The Role of AI in Drug Discovery: Challenges, Opportunities, and Strategies.",
                "10.3390/ph16060891", "PMC10302890", 2023,
                ["A. Blanco-Gonzalez", "A. Cabezon", "A. Seco-Gonzalez", "D. Conde-Torres"],
                "Artificial intelligence (AI) has the potential to revolutionize the drug discovery process, offering improved efficiency, accuracy, and speed. However, the successful application of AI is dependent on the availability of high-quality data, the addressing of ethical concerns, and the recognition of the limitations of AI-based approaches.",
            ),
            _paper(
                "Deep learning in drug discovery: an integrative review and future challenges.",
                "10.1007/s10462-022-10306-1", "PMC9669545", 2023,
                ["H. Askr", "E. Elgeldawi", "H. Aboul Ella", "Y. A. M. M. Elshaier"],
                "Recently, using artificial intelligence (AI) in drug discovery has received much attention since it significantly shortens the time and cost of developing new drugs. Deep learning (DL)-based approaches are increasingly being used in all stages of drug development as DL technology advances, and drug-related data grows.",
            ),
        ],
    },
    "assessment-standard": {
        "terms": ["organ-on-chip", "standardization", "quality control", "barrier permeability", "reproducibility"],
        "papers": [
            _paper(
                "Microfluidic organ-on-chip technology for blood-brain barrier research.",
                "10.1080/21688370.2016.1142493", "PMC4836466", 2016,
                ["M. W. van der Helm", "A. D. van der Meer", "J. C. Eijkel", "A. van den Berg"],
                "A key challenge in the field of BBB-on-chip development is the current lack of standardized quantification of parameters such as barrier permeability and shear stress. This limits the potential for direct comparison of the performance of different BBB-on-chip models to each other and existing models.",
            ),
            _paper(
                "A New Immortalized Human Alveolar Epithelial Cell Model to Study Lung Injury and Toxicity on a Breathing Lung-On-Chip System.",
                "10.3389/ftox.2022.840606", "PMC9272139", 2022,
                ["A. Sengupta", "N. Roldan", "M. Kiener", "L. Froment"],
                "In this work, we present a new and reproducible alveolar in vitro model, that combines a human derived immortalized alveolar epithelial cell line and organ-on-chip technology mimicking the lung alveolar biophysical environment. The latter mimics key features of the in vivo alveolar milieu: breathing-like 3D cyclic stretch and an ultrathin, porous and elastic membrane.",
            ),
        ],
    },
    "assessment-cost": {
        "terms": ["organ-on-chip fabrication", "scalability", "throughput", "manufacturing cost", "process standardization"],
        "papers": [
            _paper(
                "A review of organ-on-chip fabrication methods: From early developments to overcoming inert barriers.",
                "10.1016/j.isci.2025.113992", "PMC12689227", 2025,
                ["C. Olaizola-Rodrigo", "C. Bayona", "S. Olivan", "R. Monge"],
                "The fabrication methods and materials underlying these platforms play a decisive role in determining their structural accuracy, biological relevance, and potential for large-scale adoption. Alongside their benefits, the review discusses the practical challenges these approaches introduce in terms of reproducibility, throughput, and scalability.",
            ),
            _paper(
                "From Soft Lithography to 3D Printing: Current Status and Future of Microfluidic Device Fabrication.",
                "10.3390/polym17040455", "PMC11859042", 2025,
                ["J. Xu", "M. Harasek", "M. Gfohler"],
                "The advent of 3D printing has revolutionized the fabrication of microfluidic devices, offering a compelling alternative to traditional soft lithography techniques. We analyze the advantages of 3D printing in terms of cost efficiency, geometric complexity, and material versatility while addressing key challenges such as material transparency and biocompatibility.",
            ),
        ],
    },
    "assessment-data": {
        "terms": ["scientific data provenance", "reproducibility", "FAIR data", "metadata", "versioned workflow"],
        "papers": [
            _paper(
                "The role of metadata in reproducible computational research.",
                "10.1016/j.patter.2021.100322", "PMC8441584", 2021,
                ["J. Leipzig", "D. Nust", "C. T. Hoyt", "K. Ram"],
                "Metadata provide context and provenance to raw data and methods and are essential to both discovery and validation. This review employs a functional content analysis to identify metadata standards that support reproducibility across an analytic stack consisting of input data, tools, notebooks, pipelines, and publications.",
            ),
            _paper(
                "dtoolAI: Reproducibility for Deep Learning.",
                "10.1016/j.patter.2020.100073", "PMC7660391", 2020,
                ["M. Hartley", "T. S. G. Olsson"],
                "Model outputs are critically dependent on the data and processing approach used to initially generate the model, but this provenance information is usually lost during model training. The FAIR principles for data stewardship and software/workflow implementation give excellent high-level guidance on ensuring effective reuse of data and software.",
            ),
        ],
    },
}


def scenario_config(scenario_id: str) -> dict[str, Any] | None:
    return SCENARIO_CONFIGS.get(scenario_id)
